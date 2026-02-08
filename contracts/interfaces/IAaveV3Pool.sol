// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

interface IAaveV3Pool {
    function flashLoan(
        address receiverAddress,
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata modes,
        address onBehalfOf,
        bytes calldata params,
        uint16 referralCode
    ) external;
    
    function FLASHLOAN_PREMIUM_TOTAL() external view returns (uint128);
    
    function FLASHLOAN_PREMIUM_TO_PROTOCOL() external view returns (uint128);
    
    function getReserveData(address asset) external view returns (
        uint256 configuration,
        uint128 liquidityIndex,
        uint128 currentVariableBorrowIndex,
        uint128 currentStableBorrowIndex,
        uint40 lastUpdateTimestamp,
        address aTokenAddress,
        address stableDebtTokenAddress,
        address variableDebtTokenAddress,
        address interestRateStrategyAddress,
        uint128 id,
        uint128 accruedToTreasury
    );
}
