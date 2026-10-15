// SPDX-License-Identifier: MIT
pragma solidity ^0.8.22;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @notice MOCK SOMENTE PARA TESTE. NAO e o USDT real e NAO deve ser implantado
/// em producao: qualquer conta pode cunhar (mint) saldo ilimitado, sem controle
/// de acesso. Existe apenas para os testes do VendaDados terem um token de 6 casas.
contract MockUSDT is ERC20 {
    constructor() ERC20("Mock USDT (TESTE)", "mUSDT") {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}
