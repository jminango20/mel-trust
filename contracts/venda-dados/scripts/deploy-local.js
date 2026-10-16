// Deploy em node local do Hardhat (chainId 31337, 127.0.0.1:8546; a 8545 e o tunel da Besu).
// Cooperativa = endereco derivado de coop-a/keys/coop-a-besu.key (a chave NUNCA e impressa).
// So para redes de TESTE. Uso: npx hardhat compile && node scripts/deploy-local.js
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { ContractFactory, JsonRpcProvider, Wallet, parseEther } from "ethers";

const RPC = process.env.VENDA_RPC_URL ?? "http://127.0.0.1:8546";
const CHAIN_ESPERADO = 31337n;
const KEY_PATH = fileURLToPath(new URL("../../../coop-a/keys/coop-a-besu.key", import.meta.url));
const PERCENTUAL_COOP = 0n;
const PRAZO_REEMBOLSO = 30 * 24 * 60 * 60;

const artefato = (nome, caminho) =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`../artifacts/contracts/${caminho}/${nome}.json`, import.meta.url)), "utf8"));

const provider = new JsonRpcProvider(RPC);
const { chainId } = await provider.getNetwork();
if (chainId !== CHAIN_ESPERADO) {
  throw new Error(`chainId ${chainId} em ${RPC}; esperado ${CHAIN_ESPERADO} (so redes de teste)`);
}

let chave = readFileSync(KEY_PATH, "utf8").trim();
if (!chave.startsWith("0x")) chave = "0x" + chave;
const coop = new Wallet(chave, provider);

// financia a cooperativa com ETH de teste a partir da conta 0 do node
const banco = await provider.getSigner(0);
await (await banco.sendTransaction({ to: coop.address, value: parseEther("100") })).wait();

const mk = (a) => new ContractFactory(a.abi, a.bytecode, banco);
const usdt = await mk(artefato("MockUSDT", "test/MockUSDT.sol")).deploy();
await usdt.waitForDeployment();
const venda = await mk(artefato("VendaDados", "VendaDados.sol")).deploy(
  await usdt.getAddress(), coop.address, PERCENTUAL_COOP, PRAZO_REEMBOLSO,
);
await venda.waitForDeployment();

console.log(`chainId:      ${chainId}`);
console.log(`cooperativa:  ${coop.address}`);
console.log(`MockUSDT:     ${await usdt.getAddress()}`);
console.log(`VendaDados:   ${await venda.getAddress()}`);

// Endereços para quem consome o deploy (testes e coop-a leem deployments/local.json
// ou as variáveis VENDA_DADOS_*). Pasta ignorada pelo git: muda a cada node novo.
const saida = fileURLToPath(new URL("../deployments/local.json", import.meta.url));
mkdirSync(fileURLToPath(new URL("../deployments", import.meta.url)), { recursive: true });
writeFileSync(saida, JSON.stringify({
  chainId: Number(chainId),
  rpcUrl: RPC,
  cooperativa: coop.address,
  usdt: await usdt.getAddress(),
  vendaDados: await venda.getAddress(),
}, null, 2) + "\n");
console.log(`escrito:      ${saida}`);
