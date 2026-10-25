// Implantação do VendaDados (+ MockUSDT) em rede PÚBLICA de teste. Só por variáveis de ambiente:
//   DEPLOY_REDE                   nome do arquivo de saída deployments/<rede>.json; só [a-z0-9-]; nunca "local"
//   DEPLOY_RPC_URL                URL do RPC (pode levar chave de API: nunca impressa nem gravada)
//   DEPLOY_CHAIN_ID_ESPERADO      chainId que o RPC tem de ter; 31337 é recusado (essa é a rede de dev)
//   VENDA_DADOS_COOP_KEY_PATH     arquivo da chave que implanta e É a cooperativa do contrato (imutável)
//   PARTE_COOPERATIVA_PERCENTUAL  0 a 100 (padrão 0)      PRAZO_REEMBOLSO_S  inteiro > 0 (padrão 2592000)
//   BESU_KEY_PATH (opcional)      se definida, a chave da cooperativa NÃO pode ter o mesmo endereço
//   RECEIPT_TIMEOUT_S / RECEIPT_POLL_S  espera do recibo (padrões 180 e 2 neste script)
// Uso: npx hardhat compile && node scripts/deploy-publico.js
//
// Ordem: 1) validações que não precisam de rede; 2) conecta e confere eth_chainId; 3) estima o gás das duas
// implantações e exige saldo >= estimativa x preço x 1,5; 4) só então envia. Nada impresso ou gravado contém a
// URL, o host, a chave ou o caminho do arquivo da chave: as mensagens são escritas aqui (os erros do ethers,
// que trazem a URL, nunca são repassados).
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { ContractFactory, FetchRequest, JsonRpcProvider, Network, Wallet } from "ethers";

// Chaves PÚBLICAS das contas nº 0 e nº 1 do node de dev do Hardhat: qualquer um as conhece.
const CHAVES_PUBLICAS_HARDHAT = new Map([
  ["ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80", 0],
  ["59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d", 1],
]);
const CHAIN_ID_DEV = 31337n;
const PRAZO_REEMBOLSO_PADRAO_S = 2592000n;
const MARGEM_NUM = 3n; // saldo >= estimativa x preço x 1,5
const MARGEM_DEN = 2n;
const TIMEOUT_PADRAO_S = 180;
const POLL_PADRAO_S = 2;

class Recusa extends Error {} // mensagem escrita aqui, segura para imprimir

const env = process.env;

function inteiroNaoNegativo(valor, variavel, { padrao, max, minimo = 0n }) {
  if (valor === undefined || valor === "") return padrao;
  if (!/^\d+$/.test(valor)) throw new Recusa(`${variavel} inválido: precisa ser um inteiro`);
  const n = BigInt(valor);
  if (n < minimo || (max !== undefined && n > max)) {
    throw new Recusa(`${variavel} fora do intervalo permitido${max !== undefined ? ` (${minimo} a ${max})` : ` (>= ${minimo})`}`);
  }
  return n;
}

/** Chave (hex sem 0x, minúsculo) do arquivo em `caminho`; qualquer falha vira a MESMA mensagem, sem caminho. */
function lerChave(caminho, variavel) {
  const ruim = new Recusa(`chave da cooperativa ausente, ilegível ou inválida (${variavel})`);
  if (!caminho) throw ruim;
  let hex;
  try {
    hex = readFileSync(caminho, "utf8").trim().replace(/^0x/i, "").toLowerCase();
    if (!/^[0-9a-f]{64}$/.test(hex)) throw new Error("formato");
    new Wallet("0x" + hex); // valida (ex.: fora da curva)
  } catch {
    throw ruim;
  }
  return hex;
}

function validar() {
  const rede = env.DEPLOY_REDE ?? "";
  if (!/^[a-z0-9-]+$/.test(rede) || rede === "local") {
    throw new Recusa('DEPLOY_REDE inválido: use só [a-z0-9-] e nunca "local" (o local.json é do node de dev)');
  }
  if (!/^\d+$/.test(env.DEPLOY_CHAIN_ID_ESPERADO ?? "") || BigInt(env.DEPLOY_CHAIN_ID_ESPERADO) === 0n) {
    throw new Recusa("DEPLOY_CHAIN_ID_ESPERADO ausente ou inválido: informe o chainId da rede");
  }
  const chainEsperado = BigInt(env.DEPLOY_CHAIN_ID_ESPERADO);
  if (chainEsperado === CHAIN_ID_DEV) {
    throw new Recusa(`DEPLOY_CHAIN_ID_ESPERADO=${CHAIN_ID_DEV} é a rede de dev do Hardhat: use deploy-local.js`);
  }
  const percentual = inteiroNaoNegativo(env.PARTE_COOPERATIVA_PERCENTUAL, "PARTE_COOPERATIVA_PERCENTUAL", { padrao: 0n, max: 100n });
  const prazoReembolso = inteiroNaoNegativo(env.PRAZO_REEMBOLSO_S, "PRAZO_REEMBOLSO_S", { padrao: PRAZO_REEMBOLSO_PADRAO_S, minimo: 1n });
  const arquivo = fileURLToPath(new URL(`../deployments/${rede}.json`, import.meta.url));
  if (existsSync(arquivo)) {
    throw new Recusa(`deployments/${rede}.json já existe: não sobrescrevo; escolha outro DEPLOY_REDE`);
  }
  const hexCoop = lerChave(env.VENDA_DADOS_COOP_KEY_PATH, "VENDA_DADOS_COOP_KEY_PATH");
  if (CHAVES_PUBLICAS_HARDHAT.has(hexCoop)) {
    throw new Recusa(`VENDA_DADOS_COOP_KEY_PATH é a chave pública da conta nº ${CHAVES_PUBLICAS_HARDHAT.get(hexCoop)} do Hardhat: qualquer um a conhece`);
  }
  if (env.BESU_KEY_PATH) {
    let hexBesu = null;
    try {
      hexBesu = lerChave(env.BESU_KEY_PATH, "BESU_KEY_PATH");
    } catch { /* chave da Besu ausente ou ilegível: não há com o que conflitar */ }
    if (hexBesu && new Wallet("0x" + hexBesu).address === new Wallet("0x" + hexCoop).address) {
      throw new Recusa("VENDA_DADOS_COOP_KEY_PATH e BESU_KEY_PATH têm o mesmo endereço: a chave da cooperativa deve ser separada da Besu");
    }
  }
  if (!env.DEPLOY_RPC_URL) throw new Recusa("DEPLOY_RPC_URL ausente");
  return { rede, chainEsperado, percentual, prazoReembolso, arquivo, hexCoop };
}

function esperaDoRecibo() {
  const positivo = (v) => { const n = Number(v); return v !== undefined && v !== "" && Number.isFinite(n) && n > 0 ? n : null; };
  const timeout = positivo(env.RECEIPT_TIMEOUT_S) ?? TIMEOUT_PADRAO_S;
  const poll = positivo(env.RECEIPT_POLL_S) ?? POLL_PADRAO_S;
  return poll > timeout ? { timeout: TIMEOUT_PADRAO_S, poll: POLL_PADRAO_S } : { timeout, poll };
}

const artefato = (nome, caminho) =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`../artifacts/contracts/${caminho}/${nome}.json`, import.meta.url)), "utf8"));

/** Código curto do erro do ethers/rede, sem a mensagem (que traz a URL). */
const codigo = (e) => String(e?.code ?? e?.name ?? "desconhecido");

async function principal() {
  const cfg = validar();

  // --- conecta e confere o chainId (a URL só existe aqui dentro) ---
  const req = new FetchRequest(env.DEPLOY_RPC_URL);
  req.timeout = 20000;
  const provider = new JsonRpcProvider(req, Network.from(cfg.chainEsperado), { staticNetwork: Network.from(cfg.chainEsperado) });
  let chainDoNode;
  try {
    chainDoNode = BigInt(await provider.send("eth_chainId", []));
  } catch (e) {
    throw new Recusa(`não foi possível consultar o chainId do RPC (${codigo(e)})`);
  }
  if (chainDoNode !== cfg.chainEsperado) {
    throw new Recusa(`chainId do RPC (${chainDoNode}) diferente do esperado (${cfg.chainEsperado}): nada foi enviado`);
  }

  // --- estima o gás e confere o saldo ANTES de enviar ---
  const carteira = new Wallet("0x" + cfg.hexCoop, provider);
  const usdtArt = artefato("MockUSDT", "test/MockUSDT.sol");
  const vendaArt = artefato("VendaDados", "VendaDados.sol");
  const usdtFactory = new ContractFactory(usdtArt.abi, usdtArt.bytecode, carteira);
  const vendaFactory = new ContractFactory(vendaArt.abi, vendaArt.bytecode, carteira);
  let gasTotal;
  let preco;
  let saldo;
  try {
    saldo = await provider.getBalance(carteira.address);
    const dadosTaxa = await provider.getFeeData();
    preco = dadosTaxa.maxFeePerGas ?? dadosTaxa.gasPrice;
    if (saldo === 0n) throw new Recusa("saldo insuficiente: a chave da cooperativa não tem saldo nesta rede");
    // o USDT ainda não existe: o endereço da própria cooperativa serve de substituto (mesmo custo de construção)
    const txUsdt = await usdtFactory.getDeployTransaction();
    const txVenda = await vendaFactory.getDeployTransaction(carteira.address, carteira.address, cfg.percentual, cfg.prazoReembolso);
    const [g1, g2] = await Promise.all([
      provider.estimateGas({ ...txUsdt, from: carteira.address }),
      provider.estimateGas({ ...txVenda, from: carteira.address }),
    ]);
    gasTotal = g1 + g2;
  } catch (e) {
    if (e instanceof Recusa) throw e;
    throw new Recusa(`não foi possível estimar o gás das implantações (${codigo(e)}); verifique o saldo e o chainId`);
  }
  if (preco === null || preco === undefined) throw new Recusa("o RPC não informou o preço do gás: não consigo estimar o saldo necessário");
  const necessario = (gasTotal * preco * MARGEM_NUM) / MARGEM_DEN;
  if (saldo < necessario) {
    throw new Recusa(`saldo insuficiente: a chave tem ${saldo} wei e a estimativa (com margem de 50%) é ${necessario} wei; nada foi enviado`);
  }

  // --- envia (só agora) ---
  const { timeout, poll } = esperaDoRecibo();
  provider.pollingInterval = Math.round(poll * 1000);
  const esperar = async (tx, o_que) => {
    try {
      const recibo = await provider.waitForTransaction(tx.hash, 1, Math.round(timeout * 1000));
      if (!recibo || recibo.status !== 1) throw new Error("revert");
      return recibo;
    } catch (e) {
      const motivo = String(e?.code) === "TIMEOUT" ? `o recibo não veio em ${timeout}s` : "a transação falhou";
      throw new Recusa(`${o_que}: ${motivo} (txHash ${tx.hash}); confira no explorador antes de repetir`);
    }
  };
  const enviar = async (factory, args, o_que) => {
    try {
      const contrato = await factory.deploy(...args);
      return { contrato, tx: contrato.deploymentTransaction() };
    } catch (e) {
      throw new Recusa(`${o_que}: o envio falhou (${codigo(e)})`);
    }
  };
  const { tx: txUsdt } = await enviar(usdtFactory, [], "MockUSDT");
  const reciboUsdt = await esperar(txUsdt, "MockUSDT");
  const { tx: txVenda } = await enviar(vendaFactory, [reciboUsdt.contractAddress, carteira.address, cfg.percentual, cfg.prazoReembolso], "VendaDados");
  const reciboVenda = await esperar(txVenda, "VendaDados");

  // --- registro: nunca sobrescreve; sem URL, host, chave nem caminho ---
  const registro = {
    rede: cfg.rede,
    chainId: Number(chainDoNode),
    cooperativa: carteira.address,
    usdt: reciboUsdt.contractAddress,
    vendaDados: reciboVenda.contractAddress,
    blocoUsdt: reciboUsdt.blockNumber,
    blocoVendaDados: reciboVenda.blockNumber,
    txUsdt: txUsdt.hash,
    txVendaDados: txVenda.hash,
  };
  mkdirSync(resolve(cfg.arquivo, ".."), { recursive: true });
  try {
    writeFileSync(cfg.arquivo, JSON.stringify(registro, null, 2) + "\n", { flag: "wx" });
  } catch (e) {
    throw new Recusa(`implantado, mas não consegui gravar deployments/${cfg.rede}.json (${codigo(e)}); registre à mão: ${JSON.stringify(registro)}`);
  }

  console.log(`rede:         ${cfg.rede}`);
  console.log(`chainId:      ${chainDoNode}`);
  console.log(`cooperativa:  ${carteira.address}`);
  console.log(`MockUSDT:     ${registro.usdt}`);
  console.log(`VendaDados:   ${registro.vendaDados}`);
  console.log(`escrito:      deployments/${cfg.rede}.json`);
  console.log("AVISO: o MockUSDT tem emissão livre (mint aberto a qualquer conta): é só para TESTE, nunca para valor real.");
  console.log("AVISO: a cooperativa do contrato é imutável: quem assina liberar/reembolsar tem de ser a chave desta implantação.");
}

principal().catch((e) => {
  // só mensagens nossas; qualquer outro erro (que pode trazer a URL) vira uma linha genérica
  console.error(e instanceof Recusa ? `Erro: ${e.message}` : `Erro inesperado (${codigo(e)}).`);
  process.exit(1);
});
