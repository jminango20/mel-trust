// Smoke: registrarEntrega pela conta da cooperativa e leitura do saldo de cotas.
// Uso: node scripts/smoke.js <enderecoVendaDados>
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { Contract, JsonRpcProvider, Wallet, id } from "ethers";

const RPC = process.env.VENDA_RPC_URL ?? "http://127.0.0.1:8546";
const KEY_PATH = fileURLToPath(new URL("../../../coop-a/keys/coop-a-besu.key", import.meta.url));
const abi = JSON.parse(readFileSync(fileURLToPath(new URL("../artifacts/contracts/VendaDados.sol/VendaDados.json", import.meta.url)), "utf8")).abi;

const provider = new JsonRpcProvider(RPC);
let chave = readFileSync(KEY_PATH, "utf8").trim();
if (!chave.startsWith("0x")) chave = "0x" + chave;
const coop = new Wallet(chave, provider);
const venda = new Contract(process.argv[2], abi, coop);

const produtor = Wallet.createRandom().address;
const ref = id("smoke-lote-1");
console.log(`produtor: ${produtor}`);
console.log(`cotas antes:  ${await venda.balanceOf(produtor)}`);
await (await venda.registrarEntrega(produtor, ref, 1500)).wait();
console.log(`cotas depois: ${await venda.balanceOf(produtor)}`);
console.log(`totalSupply:  ${await venda.totalSupply()}`);
console.log(`entregas(ref): ${JSON.stringify((await venda.entregas(ref)).toArray().map(String))}`);
