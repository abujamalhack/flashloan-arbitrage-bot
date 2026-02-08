const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("📦 Deploying contracts with account:", deployer.address);

  // حساب تكلفة النشر
  const balance = await deployer.getBalance();
  console.log("💰 Account balance:", hre.ethers.utils.formatEther(balance), "MATIC");

  // 1. نشر العقد الرئيسي
  console.log("\n🚀 Deploying FlashLoanArbitrage...");
  const FlashLoanArbitrage = await hre.ethers.getContractFactory("FlashLoanArbitrage");
  const flashLoanArbitrage = await FlashLoanArbitrage.deploy();
  
  await flashLoanArbitrage.deployed();
  console.log("✅ FlashLoanArbitrage deployed to:", flashLoanArbitrage.address);

  // 2. حفظ عناوين العقد
  const addresses = {
    flashLoanArbitrage: flashLoanArbitrage.address,
    network: hre.network.name,
    deployer: deployer.address,
    timestamp: new Date().toISOString()
  };

  const addressesDir = path.join(__dirname, "..", "deployed");
  if (!fs.existsSync(addressesDir)) {
    fs.mkdirSync(addressesDir);
  }

  const addressesFile = path.join(addressesDir, `addresses-${hre.network.name}.json`);
  fs.writeFileSync(addressesFile, JSON.stringify(addresses, null, 2));
  
  console.log("📝 Addresses saved to:", addressesFile);

  // 3. التحقق على Polygonscan
  if (hre.network.name !== "hardhat" && hre.network.name !== "localhost") {
    console.log("\n⏳ Waiting for block confirmations...");
    await flashLoanArbitrage.deployTransaction.wait(5);
    
    console.log("🔍 Verifying contract on Polygonscan...");
    try {
      await hre.run("verify:verify", {
        address: flashLoanArbitrage.address,
        constructorArguments: [],
      });
      console.log("✅ Contract verified successfully!");
    } catch (error) {
      console.log("⚠️ Verification failed:", error.message);
    }
  }

  // 4. عرض معلومات العقد
  console.log("\n" + "=".repeat(50));
  console.log("🎉 DEPLOYMENT COMPLETE");
  console.log("=".repeat(50));
  console.log("Contract: FlashLoanArbitrage");
  console.log("Address:", flashLoanArbitrage.address);
  console.log("Deployer:", deployer.address);
  console.log("Network:", hre.network.name);
  console.log("Gas used:", flashLoanArbitrage.deployTransaction.gasLimit.toString());
  console.log("=".repeat(50));
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error("❌ Deployment failed:", error);
    process.exit(1);
  });
