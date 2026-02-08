const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  
  // تحميل عناوين العقد
  const addressesFile = path.join(__dirname, "..", "deployed", `addresses-${hre.network.name}.json`);
  const addresses = JSON.parse(fs.readFileSync(addressesFile, "utf8"));
  
  // الحصول على العقد
  const FlashLoanArbitrage = await hre.ethers.getContractFactory("FlashLoanArbitrage");
  const contract = await FlashLoanArbitrage.attach(addresses.flashLoanArbitrage);
  
  // تمويل حساب Executor
  const executorAddress = process.env.EXECUTOR_ADDRESS;
  const amount = hre.ethers.utils.parseEther("1"); // 1 MATIC
  
  console.log(`💸 Funding executor ${executorAddress} with 1 MATIC...`);
  
  const tx = await deployer.sendTransaction({
    to: executorAddress,
    value: amount
  });
  
  await tx.wait();
  console.log("✅ Executor funded successfully!");
  
  // التحقق من الرصيد
  const balance = await hre.ethers.provider.getBalance(executorAddress);
  console.log(`💰 Executor balance: ${hre.ethers.utils.formatEther(balance)} MATIC`);
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
