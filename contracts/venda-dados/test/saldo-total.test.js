// FASE RED (TDD): testes de uma view NOVA, saldoTotalSacavel(address), que ainda NAO existe no
// VendaDados.sol. Nada foi implementado.
//
// Contrato proposto (somente leitura):
//   saldoTotalSacavel(conta) = saldoSacavel[conta]
//                            + balanceOf(conta) * (acumuladoPorCota - checkpoint[conta]) / 1e18
// A parcela pendente usa a MESMA conta que _assentar usa (VendaDados.sol, l.205-210), para que o
// valor devolvido seja exatamente o que sacar() paga: sacar() chama _assentar(msg.sender) antes de
// ler saldoSacavel[msg.sender].
//
// Motivo: o getter publico saldoSacavel(conta) so enxerga o saldo JA assentado (assentar roda em
// mint, recuperarCarteira e sacar). Depois de liberar(), um produtor pode sacar mais do que
// saldoSacavel mostra, e o contrato nao tem nenhuma view que calcule o pendente.
//
// Cenario base: A = 1000 mL, B = 3000 mL (supply 4000); parte da cooperativa 10%; venda de 1000
// USDT => parteCoop 100, rateio 900 => A recebe 225, B recebe 675 (divisoes exatas).
import { expect } from "chai";
import { network } from "hardhat";

const { ethers, networkHelpers } = await network.getOrCreate();

const USDT = (n) => BigInt(n) * 10n ** 6n;
const PERCENTUAL_COOP = 10n;
const PRAZO = 7 * 24 * 60 * 60;
const REF1 = ethers.id("venda-1");
const REF2 = ethers.id("venda-2");
const LOTE_A = ethers.id("lote-A");
const LOTE_B = ethers.id("lote-B");
const LOTE_C = ethers.id("lote-C");
const LOTE_D = ethers.id("lote-D");
const LOTE_E = ethers.id("lote-E");

async function deployFixture() {
  const [, coop, comprador, prodA, prodB, prodD, intruso, nova] = await ethers.getSigners();
  const usdt = await ethers.deployContract("MockUSDT");
  const factory = await ethers.getContractFactory("VendaDados");
  const venda = await factory.deploy(
    await usdt.getAddress(), coop.address, PERCENTUAL_COOP, PRAZO,
  );
  // A = 1000 mL, B = 3000 mL
  await venda.connect(coop).registrarEntrega(prodA.address, LOTE_A, 1000);
  await venda.connect(coop).registrarEntrega(prodB.address, LOTE_B, 3000);
  return { usdt, venda, coop, comprador, prodA, prodB, prodD, intruso, nova };
}

async function depositar(f, ref, valor) {
  await f.usdt.mint(f.comprador.address, valor);
  await f.usdt.connect(f.comprador).approve(await f.venda.getAddress(), valor);
  await f.venda.connect(f.comprador).depositar(ref, valor);
}

async function vender(f, ref, valor) {
  await depositar(f, ref, valor);
  await f.venda.connect(f.coop).liberar(ref);
}

// Quanto a conta de fato recebe em USDT ao chamar sacar().
async function recebidoAoSacar(f, signer) {
  const antes = await f.usdt.balanceOf(signer.address);
  await f.venda.connect(signer).sacar();
  return (await f.usdt.balanceOf(signer.address)) - antes;
}

describe("saldoTotalSacavel(conta) (RED: a funcao ainda nao existe)", () => {
  it("(a) conta sem cotas devolve 0, antes e depois de uma venda", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    expect(await f.venda.saldoTotalSacavel(f.intruso.address)).to.equal(0n);
    await vender(f, REF1, USDT(1000));
    expect(await f.venda.saldoTotalSacavel(f.intruso.address)).to.equal(0n);
  });

  it("(b) depois de liberar, o valor devolvido e igual ao que a conta recebe em sacar()", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    await vender(f, REF1, USDT(1000));

    const totalA = await f.venda.saldoTotalSacavel(f.prodA.address);
    const totalB = await f.venda.saldoTotalSacavel(f.prodB.address);
    const totalCoop = await f.venda.saldoTotalSacavel(f.coop.address);
    expect(totalA).to.equal(USDT(225));
    expect(totalB).to.equal(USDT(675));
    expect(totalCoop).to.equal(USDT(100));

    expect(await recebidoAoSacar(f, f.prodA)).to.equal(totalA);
    expect(await recebidoAoSacar(f, f.prodB)).to.equal(totalB);
    expect(await recebidoAoSacar(f, f.coop)).to.equal(totalCoop);
  });

  it("(c) depois de sacar, devolve 0", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    await vender(f, REF1, USDT(1000));
    await f.venda.connect(f.prodA).sacar();
    expect(await f.venda.saldoTotalSacavel(f.prodA.address)).to.equal(0n);
    expect(await f.venda.saldoSacavel(f.prodA.address)).to.equal(0n);
  });

  it("(d) a cooperativa, se tiver cotas, recebe a sua parte mais o rateio", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    // cooperativa tambem detem 1000 mL => supply 5000; rateio 900 => 0,18 USDT por mL
    await f.venda.connect(f.coop).registrarEntrega(f.coop.address, LOTE_C, 1000);
    await vender(f, REF1, USDT(1000));

    const total = await f.venda.saldoTotalSacavel(f.coop.address);
    // parte da cooperativa (100) + rateio das suas 1000 cotas (180)
    expect(total).to.equal(USDT(100) + USDT(180));
    expect(await recebidoAoSacar(f, f.coop)).to.equal(total);
  });

  it("(e) depois de uma segunda venda, soma as duas", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    await vender(f, REF1, USDT(1000)); // A: 225
    await vender(f, REF2, USDT(500));  // rateio 450 => A: 1000 * 450/4000 = 112,5

    const totalA = await f.venda.saldoTotalSacavel(f.prodA.address);
    expect(totalA).to.equal(USDT(225) + 112_500_000n);
    expect(await recebidoAoSacar(f, f.prodA)).to.equal(totalA);
  });

  it("(f) conta que recebe cotas DEPOIS de uma venda nao tem pendente dessa venda", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    await vender(f, REF1, USDT(1000));
    // D chega depois da venda 1: o mint leva o checkpoint de D ao acumulado atual
    await f.venda.connect(f.coop).registrarEntrega(f.prodD.address, LOTE_D, 500);
    expect(await f.venda.saldoTotalSacavel(f.prodD.address)).to.equal(0n);

    // na venda 2 (supply 4500, rateio 900 => 0,2 USDT por mL) D participa so dela: 500 * 0,2 = 100
    await vender(f, REF2, USDT(1000));
    const totalD = await f.venda.saldoTotalSacavel(f.prodD.address);
    expect(totalD).to.equal(USDT(100));
    expect(await recebidoAoSacar(f, f.prodD)).to.equal(totalD);
  });

  it("(g) saldoSacavel publico e saldoTotalSacavel divergem antes do saque (mostra os dois numeros)", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    await vender(f, REF1, USDT(1000));

    const publico = await f.venda.saldoSacavel(f.prodA.address);
    const total = await f.venda.saldoTotalSacavel(f.prodA.address);
    console.log(`      saldoSacavel(A) publico = ${publico} | saldoTotalSacavel(A) = ${total} (unidades minimas, 6 casas)`);

    expect(publico).to.equal(0n);            // A nunca foi assentado
    expect(total).to.equal(USDT(225));       // o que A de fato pode sacar
    expect(total).to.not.equal(publico);
    // e e o total, nao o publico, que sacar() paga
    expect(await recebidoAoSacar(f, f.prodA)).to.equal(total);
  });

  it("(h) conta ja assentada (A saca) e depois uma segunda venda: so conta o rendimento da segunda", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    await vender(f, REF1, USDT(1000));
    expect(await recebidoAoSacar(f, f.prodA)).to.equal(USDT(225)); // sacar() assenta A (checkpoint avanca)

    await vender(f, REF2, USDT(500)); // rateio 450 => A: 1000 * 450/4000 = 112,5
    const total = await f.venda.saldoTotalSacavel(f.prodA.address);
    expect(total).to.equal(112_500_000n); // so a segunda venda; a primeira ja foi paga
    expect(await recebidoAoSacar(f, f.prodA)).to.equal(total);
  });

  it("(i) depois de recuperarCarteira, a nova tem o total que a antiga tinha (assentado + pendente), a antiga fica com 0 e a nova saca esse valor", async () => {
    const f = await networkHelpers.loadFixture(deployFixture);
    await vender(f, REF1, USDT(1000));
    // A recebe mais 1000 mL: o mint assenta A (saldoSacavel[A] = 225) e A passa a ter 2000 cotas (supply 5000)
    await f.venda.connect(f.coop).registrarEntrega(f.prodA.address, LOTE_E, 1000);
    expect(await f.venda.saldoSacavel(f.prodA.address)).to.equal(USDT(225));
    // segunda venda: rateio 900 / 5000 cotas = 0,18 USDT por mL => pendente de A = 2000 * 0,18 = 360
    await vender(f, REF2, USDT(1000));

    const antes = await f.venda.saldoTotalSacavel(f.prodA.address);
    expect(antes).to.equal(USDT(225) + USDT(360)); // assentado + pendente
    expect(await f.venda.saldoSacavel(f.prodA.address)).to.equal(USDT(225)); // o publico nao enxerga o pendente

    await f.venda.connect(f.coop).recuperarCarteira(f.prodA.address, f.nova.address);

    expect(await f.venda.saldoTotalSacavel(f.nova.address)).to.equal(antes);
    expect(await f.venda.saldoTotalSacavel(f.prodA.address)).to.equal(0n);
    expect(await f.venda.balanceOf(f.prodA.address)).to.equal(0n);
    expect(await f.venda.balanceOf(f.nova.address)).to.equal(2000n);
    expect(await recebidoAoSacar(f, f.nova)).to.equal(antes);
  });
});
