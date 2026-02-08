// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/security/Pausable.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts/utils/cryptography/EIP712.sol";

// Import interfaces
import "./interfaces/IAaveV3Pool.sol";
import "./interfaces/IDexRouter.sol";
import "./interfaces/IUniswapV3Router.sol";

/**
 * @title FlashLoanArbitrage - العقد الرئيسي لتنفيذ استراتيجيات المراجحة باستخدام Flash Loans
 * @dev يدعم استراتيجيات: Arbitrage, Sniping, Liquidation
 */
contract FlashLoanArbitrage is Ownable, ReentrancyGuard, Pausable, EIP712 {
    using SafeERC20 for IERC20;
    using ECDSA for bytes32;
    
    // ============ الثوابت ============
    address public constant AAVE_V3_POOL = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    uint256 public constant MIN_PROFIT_THRESHOLD = 1e15; // 0.001 MATIC
    uint256 public constant MAX_SLIPPAGE_BPS = 100; // 1%
    uint256 public constant MAX_LOAN_RATIO_BPS = 5000; // 50% من السيولة المتاحة
    
    // EIP-712 Type Hash
    bytes32 public constant EXECUTE_TYPEHASH = keccak256(
        "ExecuteFlashLoan("
        "uint8 strategy,"
        "address loanAsset,"
        "uint256 loanAmount,"
        "address dexRouter1,"
        "address dexRouter2,"
        "bytes32 buyPathHash,"
        "bytes32 sellPathHash,"
        "uint256 minOutBuy,"
        "uint256 minOutSell,"
        "uint256 minProfit,"
        "address profitToken,"
        "uint256 nonce,"
        "uint256 deadline,"
        "uint256 maxGasPrice"
        ")"
    );
    
    // ============ Enums ============
    enum Strategy { ARBITRAGE, SNIPING, LIQUIDATION }
    enum TradeStatus { PENDING, EXECUTING, SUCCESS, FAILED }
    
    // ============ الهياكل ============
    struct FlashLoanParams {
        Strategy strategy;
        address loanAsset;
        uint256 loanAmount;
        address dexRouter1;
        address dexRouter2;
        address[] buyPath;
        address[] sellPath;
        uint256 minOutBuy;
        uint256 minOutSell;
        uint256 minProfit;
        address profitToken;
        uint256 nonce;
        uint256 deadline;
        uint256 maxGasPrice;
    }
    
    struct TradeLog {
        uint256 id;
        uint256 timestamp;
        address executor;
        Strategy strategy;
        address loanAsset;
        uint256 loanAmount;
        address profitToken;
        uint256 profit;
        uint256 gasUsed;
        uint256 gasPrice;
        uint256 nonce;
        TradeStatus status;
        string errorMessage;
        bytes32 txHash;
    }
    
    struct DexConfig {
        address router;
        string name;
        bool enabled;
        bool isV3;
        address fallbackRouter;
        uint256 maxSlippageBps;
    }
    
    struct PerformanceMetrics {
        uint256 totalTrades;
        uint256 successfulTrades;
        uint256 totalProfit;
        uint256 totalGasCost;
        uint256 bestTradeProfit;
        uint256 worstTradeLoss;
        uint256 lastTradeTimestamp;
        uint256 avgExecutionTime;
    }
    
    // ============ المتغيرات العامة ============
    mapping(address => DexConfig) public dexConfigs;
    address[] public enabledRouters;
    
    mapping(uint256 => bool) public usedNonces;
    mapping(address => uint256) public userNonces;
    
    mapping(address => uint256) public profits;
    PerformanceMetrics public performance;
    
    TradeLog[] public tradeLogs;
    mapping(address => uint256[]) public userTradeLogs;
    mapping(bytes32 => uint256) public txHashToLogId;
    
    // ============ الإعدادات ============
    uint256 public maxLoanRatioBps = MAX_LOAN_RATIO_BPS;
    uint256 public minExecutionInterval = 2; // 2 blocks minimum
    uint256 public lastExecutionBlock;
    uint256 public defaultSlippageBps = 50; // 0.5%
    
    // ============ الإنذارات ============
    uint256 public profitAlertThreshold = 5 ether;
    uint256 public lossAlertThreshold = 0.5 ether;
    address public alertReceiver;
    
    // ============ الحالة الداخلية ============
    bool private _inFlashLoanExecution;
    uint256 private _currentTradeId;
    
    // ============ الأحداث ============
    event FlashLoanExecuted(
        uint256 indexed tradeId,
        address indexed executor,
        Strategy strategy,
        address loanAsset,
        uint256 loanAmount,
        uint256 profit,
        uint256 gasCost,
        uint256 timestamp
    );
    
    event TradeStatusUpdated(
        uint256 indexed tradeId,
        TradeStatus oldStatus,
        TradeStatus newStatus
    );
    
    event ArbitrageOpportunityDetected(
        address indexed detector,
        address baseAsset,
        address quoteAsset,
        uint256 expectedProfit,
        uint256 timestamp
    );
    
    event LiquidationExecuted(
        address indexed liquidator,
        address collateralAsset,
        uint256 collateralAmount,
        uint256 profit,
        uint256 timestamp
    );
    
    event ConfigurationUpdated(
        string configName,
        uint256 oldValue,
        uint256 newValue
    );
    
    event EmergencyWithdrawal(
        address indexed owner,
        address token,
        uint256 amount,
        uint256 timestamp
    );
    
    // ============ المُنشئ ============
    constructor() 
        Ownable(msg.sender)
        EIP712("FlashLoanArbitrage", "1.0.0")
    {
        _initializeDefaultRouters();
        alertReceiver = msg.sender;
    }
    
    // ============ المُعدِّلات ============
    modifier onlyDuringFlashLoan() {
        require(_inFlashLoanExecution, "Not in flash loan execution");
        _;
    }
    
    modifier onlyValidExecutor(
        bytes calldata signature, 
        FlashLoanParams calldata params
    ) {
        // التحقق من الوقت
        require(block.timestamp <= params.deadline, "Signature expired");
        require(params.deadline > block.timestamp + 60, "Deadline too short");
        
        // التحقق من Nonce
        require(!usedNonces[params.nonce], "Nonce already used");
        
        // التحقق من الربح الأدنى
        require(params.minProfit >= MIN_PROFIT_THRESHOLD, "Min profit too low");
        
        // التحقق من سعر الغاز
        require(tx.gasprice <= params.maxGasPrice, "Gas price too high");
        
        // التحقق من الفاصل الزمني
        require(
            block.number > lastExecutionBlock + minExecutionInterval,
            "Execution too frequent"
        );
        
        // التحقق من التوقيع
        _verifySignature(signature, params);
        
        // تحديث الحالة
        usedNonces[params.nonce] = true;
        lastExecutionBlock = block.number;
        
        _;
    }
    
    modifier onlyValidPath(address[] memory path) {
        require(path.length >= 2, "Invalid path length");
        for (uint i = 0; i < path.length; i++) {
            require(path[i] != address(0), "Zero address in path");
        }
        _;
    }
    
    // ============ الدوال العامة ============
    
    /**
     * @dev الدالة الرئيسية لتنفيذ Flash Loan
     * @param params معلمات التنفيذ
     * @param signature توقيع EIP-712
     */
    function executeFlashLoan(
        FlashLoanParams calldata params,
        bytes calldata signature
    ) 
        external 
        nonReentrant 
        whenNotPaused 
        onlyValidExecutor(signature, params)
        returns (uint256 tradeId)
    {
        // التحقق من الرواتر
        require(dexConfigs[params.dexRouter1].enabled, "Router 1 disabled");
        require(dexConfigs[params.dexRouter2].enabled, "Router 2 disabled");
        
        // التحقق من المسارات
        onlyValidPath(params.buyPath);
        onlyValidPath(params.sellPath);
        
        // التحقق من أن المسارات تبدأ وتنتهي بشكل صحيح
        require(
            params.buyPath[0] == params.loanAsset,
            "Buy path must start with loan asset"
        );
        require(
            params.sellPath[params.sellPath.length - 1] == params.loanAsset,
            "Sell path must end with loan asset"
        );
        
        // التحقق من السيولة المتاحة
        uint256 maxLoanAmount = _getAvailableLiquidity(params.loanAsset);
        require(
            params.loanAmount <= maxLoanAmount,
            "Loan amount exceeds available liquidity"
        );
        
        // إنشاء سجل الصفقة
        tradeId = _createTradeLog(params, msg.sender);
        
        // إعداد Flash Loan
        address[] memory assets = new address[](1);
        assets[0] = params.loanAsset;
        
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = params.loanAmount;
        
        uint256[] memory modes = new uint256[](1);
        modes[0] = 0; // No debt mode
        
        bytes memory executionParams = abi.encode(
            params,
            msg.sender,
            tradeId,
            gasleft()
        );
        
        _inFlashLoanExecution = true;
        
        try IAaveV3Pool(AAVE_V3_POOL).flashLoan(
            address(this),
            assets,
            amounts,
            modes,
            address(this),
            executionParams,
            0
        ) {
            // تحديث حالة الصفقة إلى ناجحة
            _updateTradeStatus(tradeId, TradeStatus.SUCCESS);
            
            emit FlashLoanExecuted(
                tradeId,
                msg.sender,
                params.strategy,
                params.loanAsset,
                params.loanAmount,
                profits[params.profitToken],
                tradeLogs[tradeId].gasUsed * tradeLogs[tradeId].gasPrice,
                block.timestamp
            );
            
        } catch Error(string memory reason) {
            _handleExecutionError(tradeId, reason);
            revert(string(abi.encodePacked("Execution failed: ", reason)));
        } catch {
            _handleExecutionError(tradeId, "Unknown error");
            revert("Execution failed with unknown error");
        } finally {
            _inFlashLoanExecution = false;
        }
        
        return tradeId;
    }
    
    /**
     * @dev دالة استدعاء Aave V3 Flash Loan
     */
    function executeOperation(
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata premiums,
        address initiator,
        bytes calldata params
    ) 
        external 
        onlyDuringFlashLoan
        returns (bool) 
    {
        require(msg.sender == AAVE_V3_POOL, "Caller must be Aave Pool");
        require(initiator == address(this), "Invalid initiator");
        
        (
            FlashLoanParams memory flashParams,
            address executor,
            uint256 tradeId,
            uint256 initialGas
        ) = abi.decode(params, (FlashLoanParams, address, uint256, uint256));
        
        // تحديث حالة الصفقة إلى جاري التنفيذ
        _updateTradeStatus(tradeId, TradeStatus.EXECUTING);
        
        address loanAsset = assets[0];
        uint256 loanAmount = amounts[0];
        uint256 premium = premiums[0];
        uint256 amountOwed = loanAmount + premium;
        
        try this._executeStrategy(
            flashParams,
            loanAsset,
            loanAmount,
            amountOwed
        ) {
            // النجاح - تم السداد داخل _executeStrategy
            uint256 gasUsed = initialGas - gasleft();
            uint256 gasCost = gasUsed * tx.gasprice;
            
            // تسجيل الصفقة الناجحة
            _recordSuccessfulTrade(
                tradeId,
                flashParams,
                executor,
                loanAsset,
                loanAmount,
                gasUsed,
                gasCost
            );
            
            // إرسال إنذار إذا تجاوز الربح الحد
            if (profits[flashParams.profitToken] >= profitAlertThreshold) {
                _triggerProfitAlert(tradeId, profits[flashParams.profitToken]);
            }
            
            return true;
            
        } catch Error(string memory reason) {
            _recordFailedTrade(tradeId, reason, initialGas);
            revert(string(abi.encodePacked("Strategy failed: ", reason)));
        } catch {
            _recordFailedTrade(tradeId, "Unknown error", initialGas);
            revert("Strategy failed with unknown error");
        }
    }
    
    // ============ الدوال الداخلية ============
    
    /**
     * @dev تنفيذ الاستراتيجية المحددة
     */
    function _executeStrategy(
        FlashLoanParams memory params,
        address loanAsset,
        uint256 loanAmount,
        uint256 amountOwed
    ) external {
        require(msg.sender == address(this), "Internal call only");
        
        if (params.strategy == Strategy.ARBITRAGE) {
            _executeArbitrage(params, loanAsset, loanAmount, amountOwed);
        } else if (params.strategy == Strategy.SNIPING) {
            _executeSniping(params, loanAsset, loanAmount, amountOwed);
        } else if (params.strategy == Strategy.LIQUIDATION) {
            _executeLiquidation(params, loanAsset, loanAmount, amountOwed);
        } else {
            revert("Invalid strategy");
        }
    }
    
    /**
     * @dev استراتيجية المراجحة
     */
    function _executeArbitrage(
        FlashLoanParams memory params,
        address loanAsset,
        uint256 loanAmount,
        uint256 amountOwed
    ) internal {
        // 1. الشراء من DEX الأول
        uint256 amountOut1 = _executeDexSwap(
            params.dexRouter1,
            loanAsset,
            loanAmount,
            params.minOutBuy,
            params.buyPath,
            false
        );
        
        // 2. البيع على DEX الثاني
        address intermediateToken = params.buyPath[params.buyPath.length - 1];
        uint256 finalAmount = _executeDexSwap(
            params.dexRouter2,
            intermediateToken,
            amountOut1,
            params.minOutSell,
            params.sellPath,
            true
        );
        
        // 3. التحقق من الربحية
        require(
            finalAmount >= amountOwed + params.minProfit,
            "Insufficient arbitrage profit"
        );
        
        // 4. سداد القرض
        IERC20(loanAsset).safeApprove(AAVE_V3_POOL, amountOwed);
        
        // 5. حساب وتخزين الربح
        uint256 profit = finalAmount - amountOwed;
        profits[params.profitToken] += profit;
        performance.totalProfit += profit;
        
        emit ArbitrageOpportunityDetected(
            tx.origin,
            loanAsset,
            intermediateToken,
            profit,
            block.timestamp
        );
    }
    
    /**
     * @dev استراتيجية Sniping (مبسطة)
     */
    function _executeSniping(
        FlashLoanParams memory params,
        address loanAsset,
        uint256 loanAmount,
        uint256 amountOwed
    ) internal {
        // تنفيذ مماثل للمراجحة ولكن مع شروط خاصة
        _executeArbitrage(params, loanAsset, loanAmount, amountOwed);
    }
    
    /**
     * @dev استراتيجية التصفية (Liquidation)
     */
    function _executeLiquidation(
        FlashLoanParams memory params,
        address loanAsset,
        uint256 loanAmount,
        uint256 amountOwed
    ) internal {
        // 1. شراء الأصل المتعثر بسعر منخفض
        uint256 collateralAmount = _executeDexSwap(
            params.dexRouter1,
            loanAsset,
            loanAmount,
            params.minOutBuy,
            params.buyPath,
            false
        );
        
        // 2. بيعه في السوق بسعر أعلى
        uint256 finalAmount = _executeDexSwap(
            params.dexRouter2,
            params.buyPath[params.buyPath.length - 1],
            collateralAmount,
            params.minOutSell,
            params.sellPath,
            true
        );
        
        // 3. التحقق من الربحية
        require(
            finalAmount >= amountOwed + params.minProfit,
            "Insufficient liquidation profit"
        );
        
        // 4. سداد القرض
        IERC20(loanAsset).safeApprove(AAVE_V3_POOL, amountOwed);
        
        // 5. حساب الربح
        uint256 profit = finalAmount - amountOwed;
        profits[params.profitToken] += profit;
        
        emit LiquidationExecuted(
            tx.origin,
            params.buyPath[params.buyPath.length - 1],
            collateralAmount,
            profit,
            block.timestamp
        );
    }
    
    /**
     * @dev تنفيذ مبادلة على DEX
     */
    function _executeDexSwap(
        address router,
        address fromToken,
        uint256 amountIn,
        uint256 minOut,
        address[] memory path,
        bool isSell
    ) internal returns (uint256) {
        DexConfig storage config = dexConfigs[router];
        
        // الموافقة على الرواتر
        IERC20(fromToken).safeApprove(router, amountIn);
        
        if (config.isV3) {
            // تنفيذ على Uniswap V3
            return _executeV3Swap(router, amountIn, minOut, path);
        } else {
            // تنفيذ على Uniswap V2
            return _executeV2Swap(router, amountIn, minOut, path);
        }
    }
    
    /**
     * @dev تنفيذ مبادلة على Uniswap V2
     */
    function _executeV2Swap(
        address router,
        uint256 amountIn,
        uint256 minOut,
        address[] memory path
    ) internal returns (uint256) {
        uint256 deadline = block.timestamp + 300;
        
        uint256[] memory amounts = IDexRouter(router).swapExactTokensForTokens(
            amountIn,
            minOut,
            path,
            address(this),
            deadline
        );
        
        return amounts[amounts.length - 1];
    }
    
    /**
     * @dev تنفيذ مبادلة على Uniswap V3
     */
    function _executeV3Swap(
        address router,
        uint256 amountIn,
        uint256 minOut,
        address[] memory path
    ) internal returns (uint256) {
        // بناء مسار V3 (مبسط - يحتاج إلى تحسين)
        bytes memory v3Path = abi.encodePacked(path[0]);
        for (uint i = 1; i < path.length; i++) {
            // استخدام fee tier 0.3% (يمكن تعديله)
            v3Path = abi.encodePacked(v3Path, uint24(3000), path[i]);
        }
        
        IUniswapV3Router.ExactInputParams memory params = IUniswapV3Router.ExactInputParams({
            path: v3Path,
            recipient: address(this),
            deadline: block.timestamp + 300,
            amountIn: amountIn,
            amountOutMinimum: minOut
        });
        
        return IUniswapV3Router(router).exactInput(params);
    }
    
    /**
     * @dev الحصول على السيولة المتاحة في Aave
     */
    function _getAvailableLiquidity(address asset) internal view returns (uint256) {
        try IAaveV3Pool(AAVE_V3_POOL).getReserveData(asset) returns (
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
        ) {
            // السيولة المتاحة = رصيد aToken في البروتوكول
            uint256 totalLiquidity = IERC20(aTokenAddress).totalSupply();
            
            // الحد الأقصى للقرض كنسبة من السيولة
            return totalLiquidity * maxLoanRatioBps / 10000;
        } catch {
            // في حالة الخطأ، نعود إلى قيمة افتراضية
            return 100 ether;
        }
    }
    
    /**
     * @dev التحقق من توقيع EIP-712
     */
    function _verifySignature(
        bytes calldata signature,
        FlashLoanParams calldata params
    ) internal view {
        bytes32 structHash = keccak256(abi.encode(
            EXECUTE_TYPEHASH,
            params.strategy,
            params.loanAsset,
            params.loanAmount,
            params.dexRouter1,
            params.dexRouter2,
            keccak256(abi.encodePacked(params.buyPath)),
            keccak256(abi.encodePacked(params.sellPath)),
            params.minOutBuy,
            params.minOutSell,
            params.minProfit,
            params.profitToken,
            params.nonce,
            params.deadline,
            params.maxGasPrice
        ));
        
        bytes32 digest = _hashTypedDataV4(structHash);
        address signer = ECDSA.recover(digest, signature);
        
        require(signer == owner() || signer == msg.sender, "Invalid signature");
        require(userNonces[signer] + 1 == params.nonce, "Invalid nonce");
    }
    
    /**
     * @dev إنشاء سجل صفقة جديد
     */
    function _createTradeLog(
        FlashLoanParams calldata params,
        address executor
    ) internal returns (uint256) {
        uint256 tradeId = _currentTradeId++;
        
        TradeLog memory log = TradeLog({
            id: tradeId,
            timestamp: block.timestamp,
            executor: executor,
            strategy: params.strategy,
            loanAsset: params.loanAsset,
            loanAmount: params.loanAmount,
            profitToken: params.profitToken,
            profit: 0,
            gasUsed: 0,
            gasPrice: tx.gasprice,
            nonce: params.nonce,
            status: TradeStatus.PENDING,
            errorMessage: "",
            txHash: bytes32(0)
        });
        
        tradeLogs.push(log);
        userTradeLogs[executor].push(tradeId);
        userNonces[executor] = params.nonce;
        
        return tradeId;
    }
    
    /**
     * @dev تسجيل الصفقة الناجحة
     */
    function _recordSuccessfulTrade(
        uint256 tradeId,
        FlashLoanParams memory params,
        address executor,
        address loanAsset,
        uint256 loanAmount,
        uint256 gasUsed,
        uint256 gasCost
    ) internal {
        TradeLog storage log = tradeLogs[tradeId];
        
        log.profit = profits[params.profitToken];
        log.gasUsed = gasUsed;
        log.txHash = blockhash(block.number - 1);
        
        // تحديث إحصائيات الأداء
        performance.totalTrades++;
        performance.successfulTrades++;
        performance.totalGasCost += gasCost;
        performance.lastTradeTimestamp = block.timestamp;
        
        if (log.profit > performance.bestTradeProfit) {
            performance.bestTradeProfit = log.profit;
        }
    }
    
    /**
     * @dev تسجيل الصفقة الفاشلة
     */
    function _recordFailedTrade(
        uint256 tradeId,
        string memory error,
        uint256 initialGas
    ) internal {
        TradeLog storage log = tradeLogs[tradeId];
        
        log.status = TradeStatus.FAILED;
        log.errorMessage = error;
        log.gasUsed = initialGas - gasleft();
        
        // تحديث إحصائيات الأداء
        performance.totalTrades++;
        performance.totalGasCost += log.gasUsed * log.gasPrice;
        performance.lastTradeTimestamp = block.timestamp;
        
        if (log.gasUsed * log.gasPrice > performance.worstTradeLoss) {
            performance.worstTradeLoss = log.gasUsed * log.gasPrice;
        }
        
        // إرسال إنذار إذا كانت الخسارة كبيرة
        if (log.gasUsed * log.gasPrice >= lossAlertThreshold) {
            _triggerLossAlert(tradeId, log.gasUsed * log.gasPrice);
        }
    }
    
    /**
     * @dev تحديث حالة الصفقة
     */
    function _updateTradeStatus(
        uint256 tradeId,
        TradeStatus newStatus
    ) internal {
        TradeLog storage log = tradeLogs[tradeId];
        TradeStatus oldStatus = log.status;
        log.status = newStatus;
        
        emit TradeStatusUpdated(tradeId, oldStatus, newStatus);
    }
    
    /**
     * @dev معالجة خطأ التنفيذ
     */
    function _handleExecutionError(
        uint256 tradeId,
        string memory reason
    ) internal {
        _updateTradeStatus(tradeId, TradeStatus.FAILED);
        tradeLogs[tradeId].errorMessage = reason;
    }
    
    /**
     * @dev إرسال إنذار الربح
     */
    function _triggerProfitAlert(uint256 tradeId, uint256 profit) internal {
        // يمكن إرسال إنذار إلى خدمة خارجية
        // للمثال، نرسل event فقط
    }
    
    /**
     * @dev إرسال إنذار الخسارة
     */
    function _triggerLossAlert(uint256 tradeId, uint256 loss) internal {
        // يمكن إرسال إنذار إلى خدمة خارجية
        // للمثال، نرسل event فقط
    }
    
    /**
     * @dev تهيئة الرواتر الافتراضية
     */
    function _initializeDefaultRouters() internal {
        // QuickSwap V2 (Uniswap V2)
        _addDexRouter(
            0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff,
            "QuickSwap V2",
            false,
            address(0),
            50
        );
        
        // SushiSwap
        _addDexRouter(
            0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506,
            "SushiSwap",
            false,
            0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff,
            50
        );
        
        // Uniswap V3
        _addDexRouter(
            0xE592427A0AEce92De3Edee1F18E0157C05861564,
            "Uniswap V3",
            true,
            0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff,
            30
        );
    }
    
    /**
     * @dev إضافة رواتر جديد
     */
    function _addDexRouter(
        address router,
        string memory name,
        bool isV3,
        address fallbackRouter,
        uint256 maxSlippageBps
    ) internal {
        dexConfigs[router] = DexConfig({
            router: router,
            name: name,
            enabled: true,
            isV3: isV3,
            fallbackRouter: fallbackRouter,
            maxSlippageBps: maxSlippageBps
        });
        
        enabledRouters.push(router);
    }
    
    // ============ دوال إدارة المشرف ============
    
    /**
     * @dev إضافة أو تحديث رواتر DEX
     */
    function updateDexRouter(
        address router,
        string memory name,
        bool enabled,
        bool isV3,
        address fallbackRouter,
        uint256 maxSlippageBps
    ) external onlyOwner {
        dexConfigs[router] = DexConfig({
            router: router,
            name: name,
            enabled: enabled,
            isV3: isV3,
            fallbackRouter: fallbackRouter,
            maxSlippageBps: maxSlippageBps
        });
        
        // إضافة للقائمة إذا كانت جديدة
        bool exists = false;
        for (uint i = 0; i < enabledRouters.length; i++) {
            if (enabledRouters[i] == router) {
                exists = true;
                break;
            }
        }
        
        if (!exists && enabled) {
            enabledRouters.push(router);
        }
    }
    
    /**
     * @dev تحديث إعدادات النظام
     */
    function updateConfiguration(
        uint256 newMaxLoanRatio,
        uint256 newMinInterval,
        uint256 newDefaultSlippage,
        uint256 newProfitAlert,
        uint256 newLossAlert
    ) external onlyOwner {
        if (newMaxLoanRatio > 0 && newMaxLoanRatio <= 10000) {
            emit ConfigurationUpdated("maxLoanRatioBps", maxLoanRatioBps, newMaxLoanRatio);
            maxLoanRatioBps = newMaxLoanRatio;
        }
        
        if (newMinInterval > 0) {
            emit ConfigurationUpdated("minExecutionInterval", minExecutionInterval, newMinInterval);
            minExecutionInterval = newMinInterval;
        }
        
        if (newDefaultSlippage > 0 && newDefaultSlippage <= MAX_SLIPPAGE_BPS) {
            emit ConfigurationUpdated("defaultSlippageBps", defaultSlippageBps, newDefaultSlippage);
            defaultSlippageBps = newDefaultSlippage;
        }
        
        if (newProfitAlert > 0) {
            emit ConfigurationUpdated("profitAlertThreshold", profitAlertThreshold, newProfitAlert);
            profitAlertThreshold = newProfitAlert;
        }
        
        if (newLossAlert > 0) {
            emit ConfigurationUpdated("lossAlertThreshold", lossAlertThreshold, newLossAlert);
            lossAlertThreshold = newLossAlert;
        }
    }
    
    /**
     * @dev سحب الأرباح
     */
    function withdrawProfits(address token) external onlyOwner {
        uint256 profit = profits[token];
        require(profit > 0, "No profits to withdraw");
        
        profits[token] = 0;
        IERC20(token).safeTransfer(owner(), profit);
    }
    
    /**
     * @dev دالة الطوارئ لسحب الأموال
     */
    function emergencyWithdraw(address token) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        require(balance > 0, "No balance to withdraw");
        
        IERC20(token).safeTransfer(owner(), balance);
        
        emit EmergencyWithdrawal(owner(), token, balance, block.timestamp);
    }
    
    /**
     * @dev إيقاف/تشغيل العقد
     */
    function pauseExecution() external onlyOwner {
        _pause();
    }
    
    function unpauseExecution() external onlyOwner {
        _unpause();
    }
    
    // ============ الدوال المساعدة ============
    
    /**
     * @dev محاكاة الصفقة
     */
    function simulateTrade(
        address router,
        uint256 amountIn,
        address[] calldata path
    ) external view returns (uint256 estimatedOut, bool success) {
        try IDexRouter(router).getAmountsOut(amountIn, path) returns (uint256[] memory amounts) {
            estimatedOut = amounts[amounts.length - 1];
            success = true;
        } catch {
            estimatedOut = 0;
            success = false;
        }
    }
    
    /**
     * @dev الحصول على سجل الصفقات
     */
    function getTradeLogs(
        uint256 start,
        uint256 count
    ) external view returns (TradeLog[] memory) {
        require(start < tradeLogs.length, "Invalid start");
        
        uint256 end = start + count;
        if (end > tradeLogs.length) end = tradeLogs.length;
        
        TradeLog[] memory logs = new TradeLog[](end - start);
        for (uint256 i = start; i < end; i++) {
            logs[i - start] = tradeLogs[i];
        }
        
        return logs;
    }
    
    /**
     * @dev الحصول على معلومات الأداء
     */
    function getPerformance() external view returns (PerformanceMetrics memory) {
        return performance;
    }
    
    /**
     * @dev الحصول على الرواتر المفعلة
     */
    function getEnabledRouters() external view returns (DexConfig[] memory) {
        DexConfig[] memory configs = new DexConfig[](enabledRouters.length);
        
        for (uint256 i = 0; i < enabledRouters.length; i++) {
            configs[i] = dexConfigs[enabledRouters[i]];
        }
        
        return configs;
    }
    
    receive() external payable {}
    
    fallback() external {
        revert("Invalid function call");
    }
}
