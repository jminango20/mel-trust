// Testes do VendaDados.
//
// Modelo:
//  - VendaDados e o proprio ERC20 das cotas: 1 cota = 1 mL entregue (decimals 0),
//    cunhada em registrarEntrega e NAO transferivel (so mint e recuperarCarteira).
//  - liberar() reparte por acumulador (valor por cota), nao por laco sobre produtores.
//  - A reparticao usa a oferta de cotas NO MOMENTO de liberar().
//  - A parte da cooperativa fica sacavel pela propria cooperativa via sacar().
import { expect } from "chai";
import { network } from "hardhat";

const { ethers, networkHelpers } = await network.getOrCreate();

const USDT = (n) => BigInt(n) * 10n ** 6n;
const PERCENTUAL_COOP = 10n;
const PRAZO = 7 * 24 * 60 * 60;
const REF = ethers.id("venda-1");
const REF2 = ethers.id("venda-2");
const LOTE_A = ethers.id("lote-A");
const LOTE_B = ethers.id("lote-B");

async function deployFixture() {
  const [, coop, comprador, prodA, prodB, prodC, intruso] = await ethers.getSigners();
  const usdt = await ethers.deployContract("MockUSDT");
  const factory = await ethers.getContractFactory("VendaDados");
  const venda = await factory.deploy(
    await usdt.getAddress(), coop.address, PERCENTUAL_COOP, PRAZO,
  );
  return { usdt, venda, coop, comprador, prodA, prodB, prodC, intruso };
}

async function depositar(f, ref, valor) {
  await f.usdt.mint(f.comprador.address, valor);
  await f.usdt.connect(f.comprador).approve(await f.venda.getAddress(), valor);
  return f.venda.connect(f.comprador).depositar(ref, valor);
}

// A: 1000 mL, B: 3000 mL  => A = 25%, B = 75%
async function comEntregas(f) {
  await f.venda.connect(f.coop).registrarEntrega(f.prodA.address, LOTE_A, 1000);
  await f.venda.connect(f.coop).registrarEntrega(f.prodB.address, LOTE_B, 3000);
}

describe("VendaDados", () => {
  describe("registrarEntrega", () => {
    it("so a cooperativa registra", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await expect(
        f.venda.connect(f.intruso).registrarEntrega(f.prodA.address, LOTE_A, 1000),
      ).to.be.revertedWithCustomError(f.venda, "NaoEhCooperativa");
    });

    it("cunha cotas = mililitros para o produtor", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      expect(await f.venda.balanceOf(f.prodA.address)).to.equal(1000n);
      expect(await f.venda.balanceOf(f.prodB.address)).to.equal(3000n);
      expect(await f.venda.totalSupply()).to.equal(4000n);
    });

    it("batchRef repetido com os mesmos dados e no-op (nao cunha de novo e nao reverte)", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await f.venda.connect(f.coop).registrarEntrega(f.prodA.address, LOTE_A, 1000);
      await f.venda.connect(f.coop).registrarEntrega(f.prodA.address, LOTE_A, 1000);
      expect(await f.venda.balanceOf(f.prodA.address)).to.equal(1000n);
      expect(await f.venda.totalSupply()).to.equal(1000n);
    });

    it("batchRef repetido com mililitros diferentes reverte", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await f.venda.connect(f.coop).registrarEntrega(f.prodA.address, LOTE_A, 1000);
      await expect(
        f.venda.connect(f.coop).registrarEntrega(f.prodA.address, LOTE_A, 2000),
      ).to.be.revertedWithCustomError(f.venda, "EntregaDivergente");
      expect(await f.venda.totalSupply()).to.equal(1000n);
    });

    it("batchRef repetido com outro produtor reverte", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await f.venda.connect(f.coop).registrarEntrega(f.prodA.address, LOTE_A, 1000);
      await expect(
        f.venda.connect(f.coop).registrarEntrega(f.prodB.address, LOTE_A, 1000),
      ).to.be.revertedWithCustomError(f.venda, "EntregaDivergente");
      expect(await f.venda.balanceOf(f.prodB.address)).to.equal(0n);
    });
  });

  describe("cota nao transferivel", () => {
    it("transfer reverte", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await expect(f.venda.connect(f.prodA).transfer(f.prodC.address, 1))
        .to.be.revertedWithCustomError(f.venda, "CotaNaoTransferivel");
    });

    it("transferFrom reverte, mesmo com aprovacao", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await f.venda.connect(f.prodA).approve(f.intruso.address, 1000);
      await expect(
        f.venda.connect(f.intruso).transferFrom(f.prodA.address, f.prodC.address, 1000),
      ).to.be.revertedWithCustomError(f.venda, "CotaNaoTransferivel");
    });
  });

  describe("depositar", () => {
    it("retem o valor no contrato", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      expect(await f.usdt.balanceOf(await f.venda.getAddress())).to.equal(USDT(1000));
      expect(await f.usdt.balanceOf(f.comprador.address)).to.equal(0n);
    });

    it("ref duplicada reverte", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(100));
      await expect(depositar(f, REF, USDT(100)))
        .to.be.revertedWithCustomError(f.venda, "RefDuplicada");
    });

    it("sem nenhum produtor com cota reverte", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await expect(depositar(f, REF, USDT(100)))
        .to.be.revertedWithCustomError(f.venda, "SemCotas");
    });
  });

  describe("liberar", () => {
    it("so a cooperativa libera", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await expect(f.venda.connect(f.intruso).liberar(REF))
        .to.be.revertedWithCustomError(f.venda, "NaoEhCooperativa");
      await expect(f.venda.connect(f.comprador).liberar(REF))
        .to.be.revertedWithCustomError(f.venda, "NaoEhCooperativa");
    });

    it("sem deposito reverte", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await expect(f.venda.connect(f.coop).liberar(REF))
        .to.be.revertedWithCustomError(f.venda, "SemDeposito");
    });

    it("nao libera duas vezes a mesma ref", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF);
      await expect(f.venda.connect(f.coop).liberar(REF))
        .to.be.revertedWithCustomError(f.venda, "DepositoNaoPendente");
    });

    it("desconta a parte da cooperativa e reparte o resto proporcionalmente as cotas", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF);
      // 1000 USDT: coop 10% = 100; restante 900: A (25%) = 225, B (75%) = 675
      await expect(f.venda.connect(f.coop).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.coop, USDT(100));
      await expect(f.venda.connect(f.prodA).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.prodA, USDT(225));
      await expect(f.venda.connect(f.prodB).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.prodB, USDT(675));
    });

    it("acumula varias vendas", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await depositar(f, REF2, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF);
      await f.venda.connect(f.coop).liberar(REF2);
      await expect(f.venda.connect(f.prodA).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.prodA, USDT(450));
    });
  });

  describe("sacar", () => {
    it("saque duplo nao paga duas vezes", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF);
      await f.venda.connect(f.prodA).sacar();
      const saldo = await f.usdt.balanceOf(f.prodA.address);
      await expect(f.venda.connect(f.prodA).sacar())
        .to.be.revertedWithCustomError(f.venda, "SemSaldo");
      expect(await f.usdt.balanceOf(f.prodA.address)).to.equal(saldo);
    });

    it("quem nao tem saldo nao saca", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF);
      await expect(f.venda.connect(f.intruso).sacar())
        .to.be.revertedWithCustomError(f.venda, "SemSaldo");
    });
  });

  describe("invariante de saques", () => {
    it("a soma dos saques (produtores e cooperativa) nunca passa do total depositado; reporta a poeira", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      // cotas 7, 11, 13 e valores "feios" para forcar arredondamento
      await f.venda.connect(f.coop).registrarEntrega(f.prodA.address, LOTE_A, 7);
      await f.venda.connect(f.coop).registrarEntrega(f.prodB.address, LOTE_B, 11);
      await f.venda.connect(f.coop).registrarEntrega(f.prodC.address, ethers.id("lote-C"), 13);

      const valores = [1001n, 777777n, 12345679n];
      let totalDepositado = 0n;
      for (let i = 0; i < valores.length; i++) {
        const ref = ethers.id(`venda-inv-${i}`);
        await depositar(f, ref, valores[i]);
        await f.venda.connect(f.coop).liberar(ref);
        totalDepositado += valores[i];
      }

      for (const quem of [f.prodA, f.prodB, f.prodC, f.coop]) {
        await f.venda.connect(quem).sacar();
      }
      let totalSacado = 0n;
      for (const quem of [f.prodA, f.prodB, f.prodC, f.coop]) {
        totalSacado += await f.usdt.balanceOf(quem.address);
      }
      const poeira = await f.usdt.balanceOf(await f.venda.getAddress());

      console.log(`      depositado=${totalDepositado} sacado=${totalSacado} poeira no contrato=${poeira}`);
      expect(totalSacado).to.be.lessThanOrEqual(totalDepositado);
      expect(totalSacado + poeira).to.equal(totalDepositado); // nada some nem aparece
      // cada venda perde < 1 unidade minima por produtor (piso do saldo) + < 1 no rateio
      // (a parte da cooperativa e exata): limite = (produtores + 1) * vendas
      const limite = BigInt(3 + 1) * BigInt(valores.length);
      expect(poeira).to.be.lessThanOrEqual(limite);
    });
  });

  describe("casos de borda de cotas e saldos", () => {
    it("cooperativa como detentora de cotas recebe a parte de cota e a parte da cooperativa e saca as duas", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await f.venda.connect(f.coop).registrarEntrega(f.coop.address, LOTE_A, 1000);
      await f.venda.connect(f.coop).registrarEntrega(f.prodA.address, LOTE_B, 1000);
      await depositar(f, REF, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF);
      // coop: 10% = 100 + 50% de 900 = 450 => 550. prodA: 450.
      await expect(f.venda.connect(f.coop).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.coop, USDT(550));
      await expect(f.venda.connect(f.prodA).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.prodA, USDT(450));
    });

    it("produtor que saca e depois recebe novas cotas: nova venda soma corretamente", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await f.venda.connect(f.coop).registrarEntrega(f.prodA.address, LOTE_A, 1000);
      await f.venda.connect(f.coop).registrarEntrega(f.prodB.address, LOTE_B, 1000);
      await depositar(f, REF, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF);
      await expect(f.venda.connect(f.prodA).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.prodA, USDT(450));
      // A passa a ter 2000 de 3000 cotas
      await f.venda.connect(f.coop).registrarEntrega(f.prodA.address, ethers.id("lote-A2"), 1000);
      await depositar(f, REF2, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF2);
      await expect(f.venda.connect(f.prodA).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.prodA, USDT(600)); // 2/3 de 900
      await expect(f.venda.connect(f.prodB).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.prodB, USDT(750)); // 450 + 1/3 de 900
    });

    it("produtor com saldo nao sacado que recebe novas cotas preserva o saldo antigo", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await f.venda.connect(f.coop).registrarEntrega(f.prodA.address, LOTE_A, 1000);
      await f.venda.connect(f.coop).registrarEntrega(f.prodB.address, LOTE_B, 1000);
      await depositar(f, REF, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF);
      await f.venda.connect(f.coop).registrarEntrega(f.prodA.address, ethers.id("lote-A2"), 1000);
      await depositar(f, REF2, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF2);
      await expect(f.venda.connect(f.prodA).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.prodA, USDT(1050)); // 450 + 600
    });
  });

  describe("cota recebida depois de uma venda", () => {
    it("nao participa dela, mas participa das seguintes", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await f.venda.connect(f.coop).registrarEntrega(f.prodA.address, LOTE_A, 1000);
      await depositar(f, REF, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF);
      // B recebe cota DEPOIS da venda 1 (mesma quantidade de A)
      await f.venda.connect(f.coop).registrarEntrega(f.prodB.address, LOTE_B, 1000);

      await expect(f.venda.connect(f.prodB).sacar())
        .to.be.revertedWithCustomError(f.venda, "SemSaldo"); // nada da venda 1

      await depositar(f, REF2, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF2);
      // venda 1: A leva 900 (1000 - 10%). venda 2: 900 divididos 50/50 => 450 cada.
      await expect(f.venda.connect(f.prodA).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.prodA, USDT(1350));
      await expect(f.venda.connect(f.prodB).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.prodB, USDT(450));
    });
  });

  describe("reembolso", () => {
    it("so a cooperativa reembolsa", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await expect(f.venda.connect(f.intruso).reembolsar(REF))
        .to.be.revertedWithCustomError(f.venda, "NaoEhCooperativa");
      await expect(f.venda.connect(f.comprador).reembolsar(REF))
        .to.be.revertedWithCustomError(f.venda, "NaoEhCooperativa");
    });

    it("a cooperativa devolve o deposito ao comprador", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await expect(f.venda.connect(f.coop).reembolsar(REF))
        .to.changeTokenBalance(ethers, f.usdt, f.comprador, USDT(1000));
    });
  });

  describe("recuperarReembolso", () => {
    it("antes do prazo reverte", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await networkHelpers.time.increase(PRAZO - 100);
      await expect(f.venda.connect(f.comprador).recuperarReembolso(REF))
        .to.be.revertedWithCustomError(f.venda, "PrazoNaoVencido");
    });

    it("depois do prazo o comprador recupera, e so uma vez", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await networkHelpers.time.increase(PRAZO + 1);
      await expect(f.venda.connect(f.comprador).recuperarReembolso(REF))
        .to.changeTokenBalance(ethers, f.usdt, f.comprador, USDT(1000));
      await expect(f.venda.connect(f.comprador).recuperarReembolso(REF))
        .to.be.revertedWithCustomError(f.venda, "DepositoNaoPendente");
    });

    it("so o comprador que depositou recupera", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await networkHelpers.time.increase(PRAZO + 1);
      await expect(f.venda.connect(f.intruso).recuperarReembolso(REF))
        .to.be.revertedWithCustomError(f.venda, "NaoEhComprador");
    });
  });

  describe("maquina de estados do deposito", () => {
    it("depois de liberar: reembolsar, recuperarReembolso e liberar revertem", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF);
      await networkHelpers.time.increase(PRAZO + 1);
      await expect(f.venda.connect(f.coop).reembolsar(REF))
        .to.be.revertedWithCustomError(f.venda, "DepositoNaoPendente");
      await expect(f.venda.connect(f.comprador).recuperarReembolso(REF))
        .to.be.revertedWithCustomError(f.venda, "DepositoNaoPendente");
      await expect(f.venda.connect(f.coop).liberar(REF))
        .to.be.revertedWithCustomError(f.venda, "DepositoNaoPendente");
    });

    it("depois de reembolsar: liberar, reembolsar e recuperarReembolso revertem", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await f.venda.connect(f.coop).reembolsar(REF);
      await networkHelpers.time.increase(PRAZO + 1);
      await expect(f.venda.connect(f.coop).liberar(REF))
        .to.be.revertedWithCustomError(f.venda, "DepositoNaoPendente");
      await expect(f.venda.connect(f.coop).reembolsar(REF))
        .to.be.revertedWithCustomError(f.venda, "DepositoNaoPendente");
      await expect(f.venda.connect(f.comprador).recuperarReembolso(REF))
        .to.be.revertedWithCustomError(f.venda, "DepositoNaoPendente");
    });

    it("depois de recuperarReembolso: liberar e reembolsar revertem", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await networkHelpers.time.increase(PRAZO + 1);
      await f.venda.connect(f.comprador).recuperarReembolso(REF);
      await expect(f.venda.connect(f.coop).liberar(REF))
        .to.be.revertedWithCustomError(f.venda, "DepositoNaoPendente");
      await expect(f.venda.connect(f.coop).reembolsar(REF))
        .to.be.revertedWithCustomError(f.venda, "DepositoNaoPendente");
    });
  });

  describe("recuperarCarteira", () => {
    it("so a cooperativa recupera", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await expect(
        f.venda.connect(f.intruso).recuperarCarteira(f.prodA.address, f.prodC.address),
      ).to.be.revertedWithCustomError(f.venda, "NaoEhCooperativa");
    });

    it("move as cotas e o saldo sacavel para a carteira nova", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await depositar(f, REF, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF); // A tem 225 sacaveis

      await f.venda.connect(f.coop).recuperarCarteira(f.prodA.address, f.prodC.address);

      expect(await f.venda.balanceOf(f.prodA.address)).to.equal(0n);
      expect(await f.venda.balanceOf(f.prodC.address)).to.equal(1000n);
      await expect(f.venda.connect(f.prodA).sacar())
        .to.be.revertedWithCustomError(f.venda, "SemSaldo");
      await expect(f.venda.connect(f.prodC).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.prodC, USDT(225));

      // vendas seguintes vao para a carteira nova
      await depositar(f, REF2, USDT(1000));
      await f.venda.connect(f.coop).liberar(REF2);
      await expect(f.venda.connect(f.prodC).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.prodC, USDT(225));
    });
  });

  describe("recuperarCarteira: casos de borda", () => {
    it("conta sem cotas e sem saldo reverte com CarteiraInvalida", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      await comEntregas(f);
      await expect(f.venda.connect(f.coop).recuperarCarteira(f.intruso.address, f.prodC.address))
        .to.be.revertedWithCustomError(f.venda, "CarteiraInvalida");
    });

    it("carteira nova que ja tem cotas e saldo: totais se conservam e cada conta saca o devido", async () => {
      const f = await networkHelpers.loadFixture(deployFixture);
      const coop = f.venda.connect(f.coop);
      await coop.registrarEntrega(f.prodA.address, LOTE_A, 1000);
      await coop.registrarEntrega(f.prodB.address, LOTE_B, 3000);
      await depositar(f, REF, USDT(1000));
      await coop.liberar(REF); // A 225, B 675, coop 100 (A ainda nao assentado)
      await coop.registrarEntrega(f.prodC.address, ethers.id("lote-C"), 1000);
      await depositar(f, REF2, USDT(1000));
      await coop.liberar(REF2); // 900 sobre 5000: A 180, B 540, C 180
      // novo mint assenta C: guarda os 180 dele no saldo sacavel
      await coop.registrarEntrega(f.prodC.address, ethers.id("lote-C2"), 500);
      expect(await f.venda.saldoSacavel(f.prodC.address)).to.equal(USDT(180));

      const supplyAntes = await f.venda.totalSupply();
      const cotasAntes = (await f.venda.balanceOf(f.prodA.address)) + (await f.venda.balanceOf(f.prodC.address));

      await coop.recuperarCarteira(f.prodA.address, f.prodC.address);

      expect(await f.venda.totalSupply()).to.equal(supplyAntes);
      expect(await f.venda.balanceOf(f.prodA.address)).to.equal(0n);
      expect(await f.venda.balanceOf(f.prodC.address)).to.equal(cotasAntes); // 1000 + 1500
      // A: 225 + 180 = 405 movidos; C: 180 + 405 = 585
      expect(await f.venda.saldoSacavel(f.prodA.address)).to.equal(0n);
      expect(await f.venda.saldoSacavel(f.prodC.address)).to.equal(USDT(585));

      await expect(f.venda.connect(f.prodA).sacar())
        .to.be.revertedWithCustomError(f.venda, "SemSaldo");
      await expect(f.venda.connect(f.prodC).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.prodC, USDT(585));
      await expect(f.venda.connect(f.prodB).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.prodB, USDT(1215)); // 675 + 540
      await expect(f.venda.connect(f.coop).sacar())
        .to.changeTokenBalance(ethers, f.usdt, f.coop, USDT(200));

      // venda seguinte: C, com 2500 de 5500 cotas, recebe 2500/5500 de 900
      await depositar(f, ethers.id("venda-3"), USDT(1000));
      await coop.liberar(ethers.id("venda-3"));
      const esperado = (USDT(900) * 2500n) / 5500n;
      const antes = await f.usdt.balanceOf(f.prodC.address);
      await f.venda.connect(f.prodC).sacar();
      const recebido = (await f.usdt.balanceOf(f.prodC.address)) - antes;
      expect(recebido).to.be.within(esperado - 2n, esperado);
    });
  });

  describe("gas de liberar nao cresce com o numero de produtores", () => {
    // PRIMEIRO liberar de um deploy novo: mesma situacao de armazenamento nos dois casos
    // (acumuladoPorCota e saldoSacavel[cooperativa] saem de zero).
    async function gasDoPrimeiroLiberarCom(n) {
      const f = await deployFixture();
      for (let i = 0; i < n; i++) {
        const produtor = ethers.Wallet.createRandom().address;
        await f.venda.connect(f.coop).registrarEntrega(produtor, ethers.id(`lote-${n}-${i}`), 1000);
      }
      await depositar(f, REF, USDT(1000));
      const tx = await f.venda.connect(f.coop).liberar(REF);
      return (await tx.wait()).gasUsed;
    }

    it("primeiro liberar com 3 e com 30 produtores custa praticamente o mesmo", async function () {
      this.timeout(120000);
      const g3 = await gasDoPrimeiroLiberarCom(3);
      const g30 = await gasDoPrimeiroLiberarCom(30);
      console.log(`      gas do primeiro liberar: 3 produtores = ${g3}, 30 produtores = ${g30}`);
      const diferenca = g30 > g3 ? g30 - g3 : g3 - g30;
      expect(diferenca * 100n / g3).to.be.lessThan(1n); // diferenca < 1%
    });
  });
});
