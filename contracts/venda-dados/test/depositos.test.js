// Testes do getter depositos(ref) e do formato da ref que o coop-a produz.
//
// depositos(bytes32) devolve (comprador, valor, depositadoEm, estado):
//   estado: 0 Nenhum, 1 Depositado, 2 Liberado, 3 Reembolsado.
// Reembolsar (cooperativa) e recuperarReembolso (comprador, apos o prazo) levam ambos a 3:
// o contrato nao distingue quem reembolsou no estado, so no msg.sender da tx.
import { expect } from "chai";
import { network } from "hardhat";

const { ethers, networkHelpers } = await network.getOrCreate();

const USDT = (n) => BigInt(n) * 10n ** 6n;
const PERCENTUAL_COOP = 10n;
const PRAZO = 7 * 24 * 60 * 60;
const REF = ethers.id("venda-1");
const LOTE_A = ethers.id("lote-A");
const ZERO = ethers.ZeroAddress;
const Estado = { Nenhum: 0n, Depositado: 1n, Liberado: 2n, Reembolsado: 3n };

async function deployFixture() {
  const [, coop, comprador, prodA, intruso] = await ethers.getSigners();
  const usdt = await ethers.deployContract("MockUSDT");
  const factory = await ethers.getContractFactory("VendaDados");
  const venda = await factory.deploy(
    await usdt.getAddress(), coop.address, PERCENTUAL_COOP, PRAZO,
  );
  await venda.connect(coop).registrarEntrega(prodA.address, LOTE_A, 1000);
  return { usdt, venda, coop, comprador, prodA, intruso };
}

async function depositar(f, ref, valor) {
  await f.usdt.mint(f.comprador.address, valor);
  await f.usdt.connect(f.comprador).approve(await f.venda.getAddress(), valor);
  return f.venda.connect(f.comprador).depositar(ref, valor);
}

async function timestampDoBloco(tx) {
  const receipt = await tx.wait();
  return BigInt((await ethers.provider.getBlock(receipt.blockNumber)).timestamp);
}

describe("depositos(ref)", () => {
  it("ref desconhecida devolve (0x0, 0, 0, estado 0)", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    const d = await f.venda.depositos(ethers.id("nunca-depositada"));
    expect(d.comprador).to.equal(ZERO);
    expect(d.valor).to.equal(0n);
    expect(d.depositadoEm).to.equal(0n);
    expect(d.estado).to.equal(Estado.Nenhum);
  });

  it("depois de depositar: comprador, valor, timestamp do bloco e estado 1", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    const tx = await depositar(f, REF, USDT(1000));
    const ts = await timestampDoBloco(tx);
    const d = await f.venda.depositos(REF);
    expect(d.comprador).to.equal(f.comprador.address);
    expect(d.valor).to.equal(USDT(1000));
    expect(d.depositadoEm).to.equal(ts);
    expect(d.estado).to.equal(Estado.Depositado);
  });

  it("o evento Depositado carrega (ref indexada, comprador indexado, valor)", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    await expect(depositar(f, REF, USDT(1000)))
      .to.emit(f.venda, "Depositado")
      .withArgs(REF, f.comprador.address, USDT(1000));
  });

  it("depois de liberar: estado 2, comprador e valor preservados", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    const tx = await depositar(f, REF, USDT(1000));
    const ts = await timestampDoBloco(tx);
    await f.venda.connect(f.coop).liberar(REF);
    const d = await f.venda.depositos(REF);
    expect(d.comprador).to.equal(f.comprador.address);
    expect(d.valor).to.equal(USDT(1000));
    expect(d.depositadoEm).to.equal(ts);
    expect(d.estado).to.equal(Estado.Liberado);
  });

  it("depois de reembolsar (cooperativa): estado 3, comprador e valor preservados", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    await depositar(f, REF, USDT(1000));
    await f.venda.connect(f.coop).reembolsar(REF);
    const d = await f.venda.depositos(REF);
    expect(d.comprador).to.equal(f.comprador.address);
    expect(d.valor).to.equal(USDT(1000));
    expect(d.estado).to.equal(Estado.Reembolsado);
  });

  it("depois de recuperarReembolso (comprador, apos o prazo): estado 3, comprador e valor preservados", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    await depositar(f, REF, USDT(1000));
    await networkHelpers.time.increase(PRAZO + 1);
    await f.venda.connect(f.comprador).recuperarReembolso(REF);
    const d = await f.venda.depositos(REF);
    expect(d.comprador).to.equal(f.comprador.address);
    expect(d.valor).to.equal(USDT(1000));
    expect(d.estado).to.equal(Estado.Reembolsado);
  });

  it("deposito de um comprador nao aparece em outra ref", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    await depositar(f, REF, USDT(1000));
    const d = await f.venda.depositos(ethers.id("venda-2"));
    expect(d.comprador).to.equal(ZERO);
    expect(d.estado).to.equal(Estado.Nenhum);
  });
});

describe("ref no formato do coop-a (keccak256 do JSON canonico, hex 0x + 64)", () => {
  // O coop-a calcula contractRef = "0x" + keccak256(utf8(json canonico)) em hex.
  const json = '{"buyerWallet":"0x0000000000000000000000000000000000000001","consumer":"Cooperativa Ágil",'
    + '"priceUnits":2500000,"purpose":"Compliance EUDR","validFrom":"2026-11-01T00:00:00Z",'
    + '"validUntil":"2027-11-01T00:00:00Z"}';
  const contractRef = ethers.keccak256(ethers.toUtf8Bytes(json));

  it("tem o formato 0x + 64 caracteres hexadecimais", () => {
    expect(contractRef).to.match(/^0x[0-9a-f]{64}$/);
  });

  it("depositar aceita a ref como string hex e depositos(ref) a encontra", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    await expect(depositar(f, contractRef, USDT(2)))
      .to.emit(f.venda, "Depositado")
      .withArgs(contractRef, f.comprador.address, USDT(2));
    const d = await f.venda.depositos(contractRef);
    expect(d.comprador).to.equal(f.comprador.address);
    expect(d.valor).to.equal(USDT(2));
    expect(d.estado).to.equal(Estado.Depositado);
  });

  it("a mesma ref e a mesma depois de recalculada (determinismo) e repeti-la reverte", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    await depositar(f, contractRef, USDT(2));
    const recalculada = ethers.keccak256(ethers.toUtf8Bytes(json));
    expect(recalculada).to.equal(contractRef);
    await expect(depositar(f, recalculada, USDT(2)))
      .to.be.revertedWithCustomError(f.venda, "RefDuplicada");
  });
});
