// SPDX-License-Identifier: MIT
pragma solidity ^0.8.22;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title VendaDados
/// @notice Escrow de venda de dados com repartição proporcional às cotas dos produtores.
///
/// Como funciona:
///  - A cooperativa registra entregas (`registrarEntrega`); cada mililitro entregue vira
///    1 cota (ERC20 com 0 casas, NÃO transferível: só mint e `recuperarCarteira`).
///  - O comprador deposita USDT com uma `ref` única (`depositar`); o valor fica retido.
///  - A cooperativa libera (`liberar`): desconta a parte da cooperativa e reparte o resto
///    entre as cotas EXISTENTES naquele momento.
///  - Cada um retira o que acumulou com `sacar`.
///
/// Repartição O(1): `liberar` não percorre produtores. Ele só aumenta `acumuladoPorCota`
/// (valor por cota, escalado por PRECISAO). O saldo de cada conta é "assentado" de forma
/// preguiçosa: sempre que as cotas de uma conta mudam (mint, recuperarCarteira) ou ela
/// saca, `_assentar` soma `cotas * (acumulado - checkpoint)` ao saldo sacável e avança o
/// checkpoint. Por isso cota recebida DEPOIS de uma venda não participa dela: no momento
/// do mint o checkpoint da conta já é levado ao acumulado atual.
///
/// Poeira: divisões inteiras (piso) deixam alguns centavos de unidade mínima do USDT presos
/// no contrato. Não há função para retirá-los: ficam no contrato para sempre. Por venda, a
/// perda é menor que (nº de contas com cota + 1) unidades mínimas. A soma dos saques nunca
/// ultrapassa a soma dos depósitos.
contract VendaDados is ERC20, ReentrancyGuard {
    using SafeERC20 for IERC20;

    enum Estado {
        Nenhum,
        Depositado,
        Liberado,
        Reembolsado
    }

    struct Entrega {
        address produtor;
        uint256 mililitros;
    }

    struct Deposito {
        address comprador;
        uint256 valor;
        uint64 depositadoEm;
        Estado estado;
    }

    error NaoEhCooperativa();
    error ParametroInvalido();
    error CotaNaoTransferivel();
    error EntregaInvalida();
    error EntregaDivergente(bytes32 batchRef);
    error SemCotas();
    error ValorZero();
    error RefDuplicada(bytes32 ref);
    error SemDeposito(bytes32 ref);
    error DepositoNaoPendente(bytes32 ref, Estado estado);
    error NaoEhComprador();
    error PrazoNaoVencido(uint256 liberadoEm);
    error SemSaldo();
    error CarteiraInvalida();

    event EntregaRegistrada(bytes32 indexed batchRef, address indexed produtor, uint256 mililitros);
    event Depositado(bytes32 indexed ref, address indexed comprador, uint256 valor);
    event Liberado(bytes32 indexed ref, uint256 parteCooperativa, uint256 rateio);
    event Reembolsado(bytes32 indexed ref, address indexed comprador, uint256 valor);
    event Sacado(address indexed conta, uint256 valor);
    event CarteiraRecuperada(address indexed antiga, address indexed nova, uint256 cotas, uint256 saldo);

    uint256 private constant PRECISAO = 1e18;

    IERC20 public immutable usdt;
    address public immutable cooperativa;
    uint256 public immutable parteCooperativaPercentual;
    uint256 public immutable prazoReembolso;

    uint256 public acumuladoPorCota;
    mapping(address => uint256) public checkpoint;
    mapping(address => uint256) public saldoSacavel;
    mapping(bytes32 => Entrega) public entregas;
    mapping(bytes32 => Deposito) public depositos;

    bool private _movendoCarteira;

    modifier soCooperativa() {
        if (msg.sender != cooperativa) revert NaoEhCooperativa();
        _;
    }

    constructor(
        address usdt_,
        address cooperativa_,
        uint256 parteCooperativaPercentual_,
        uint256 prazoReembolsoSegundos
    ) ERC20("Cota VendaDados", "COTA") {
        if (usdt_ == address(0) || cooperativa_ == address(0)) revert ParametroInvalido();
        if (parteCooperativaPercentual_ > 100 || prazoReembolsoSegundos == 0) revert ParametroInvalido();
        usdt = IERC20(usdt_);
        cooperativa = cooperativa_;
        parteCooperativaPercentual = parteCooperativaPercentual_;
        prazoReembolso = prazoReembolsoSegundos;
    }

    /// @dev 1 cota = 1 mL: sem casas decimais.
    function decimals() public pure override returns (uint8) {
        return 0;
    }

    // --- Entrega e cotas -----------------------------------------------------

    /// @notice Registra a entrega de um lote e cunha cotas = mililitros. Idempotente por
    /// `batchRef`: repetir com os mesmos dados não faz nada; com dados diferentes reverte.
    function registrarEntrega(address produtor, bytes32 batchRef, uint256 mililitros) external soCooperativa {
        if (produtor == address(0) || mililitros == 0) revert EntregaInvalida();
        Entrega memory existente = entregas[batchRef];
        if (existente.produtor != address(0)) {
            if (existente.produtor == produtor && existente.mililitros == mililitros) return;
            revert EntregaDivergente(batchRef);
        }
        entregas[batchRef] = Entrega(produtor, mililitros);
        _mint(produtor, mililitros);
        emit EntregaRegistrada(batchRef, produtor, mililitros);
    }

    // --- Fluxo da venda ------------------------------------------------------

    function depositar(bytes32 ref, uint256 valor) external nonReentrant {
        if (valor == 0) revert ValorZero();
        if (depositos[ref].estado != Estado.Nenhum) revert RefDuplicada(ref);
        if (totalSupply() == 0) revert SemCotas();
        depositos[ref] = Deposito(msg.sender, valor, uint64(block.timestamp), Estado.Depositado);
        usdt.safeTransferFrom(msg.sender, address(this), valor);
        emit Depositado(ref, msg.sender, valor);
    }

    function liberar(bytes32 ref) external soCooperativa nonReentrant {
        Deposito storage d = _depositoPendente(ref);
        d.estado = Estado.Liberado;
        uint256 parteCoop = (d.valor * parteCooperativaPercentual) / 100;
        uint256 rateio = d.valor - parteCoop;
        // totalSupply() > 0 garantido: a oferta nunca diminui e depositar exige oferta > 0.
        acumuladoPorCota += (rateio * PRECISAO) / totalSupply();
        saldoSacavel[cooperativa] += parteCoop;
        emit Liberado(ref, parteCoop, rateio);
    }

    function reembolsar(bytes32 ref) external soCooperativa nonReentrant {
        Deposito storage d = _depositoPendente(ref);
        d.estado = Estado.Reembolsado;
        usdt.safeTransfer(d.comprador, d.valor);
        emit Reembolsado(ref, d.comprador, d.valor);
    }

    function recuperarReembolso(bytes32 ref) external nonReentrant {
        Deposito storage d = _depositoPendente(ref);
        if (msg.sender != d.comprador) revert NaoEhComprador();
        uint256 liberadoEm = uint256(d.depositadoEm) + prazoReembolso;
        if (block.timestamp < liberadoEm) revert PrazoNaoVencido(liberadoEm);
        d.estado = Estado.Reembolsado;
        usdt.safeTransfer(d.comprador, d.valor);
        emit Reembolsado(ref, d.comprador, d.valor);
    }

    function sacar() external nonReentrant {
        _assentar(msg.sender);
        uint256 valor = saldoSacavel[msg.sender];
        if (valor == 0) revert SemSaldo();
        saldoSacavel[msg.sender] = 0;
        usdt.safeTransfer(msg.sender, valor);
        emit Sacado(msg.sender, valor);
    }

    /// @notice Troca de carteira (chave perdida): move cotas e saldo sacável.
    function recuperarCarteira(address antiga, address nova) external soCooperativa {
        if (nova == address(0) || antiga == nova) revert CarteiraInvalida();
        _assentar(antiga);
        _assentar(nova);
        uint256 cotas = balanceOf(antiga);
        uint256 saldo = saldoSacavel[antiga];
        if (cotas == 0 && saldo == 0) revert CarteiraInvalida();
        saldoSacavel[antiga] = 0;
        saldoSacavel[nova] += saldo;
        if (cotas > 0) {
            _movendoCarteira = true;
            _update(antiga, nova, cotas);
            _movendoCarteira = false;
        }
        emit CarteiraRecuperada(antiga, nova, cotas, saldo);
    }

    // --- Internos ------------------------------------------------------------

    function _depositoPendente(bytes32 ref) private view returns (Deposito storage d) {
        d = depositos[ref];
        if (d.estado == Estado.Nenhum) revert SemDeposito(ref);
        if (d.estado != Estado.Depositado) revert DepositoNaoPendente(ref, d.estado);
    }

    /// @dev Soma ao saldo sacável o que as cotas atuais da conta renderam desde o checkpoint.
    function _assentar(address conta) private {
        uint256 atual = acumuladoPorCota;
        uint256 pendente = (balanceOf(conta) * (atual - checkpoint[conta])) / PRECISAO;
        if (pendente != 0) saldoSacavel[conta] += pendente;
        checkpoint[conta] = atual;
    }

    /// @dev Só mint (from == 0) e `recuperarCarteira` movem cotas. As contas são assentadas
    /// ANTES de a cota mudar, para a venda passada seguir o dono antigo da cota.
    function _update(address from, address to, uint256 value) internal override {
        if (from != address(0) && !_movendoCarteira) revert CotaNaoTransferivel();
        if (from != address(0)) _assentar(from);
        if (to != address(0)) _assentar(to);
        super._update(from, to, value);
    }
}
