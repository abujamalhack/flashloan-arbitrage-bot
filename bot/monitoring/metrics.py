"""
مجمع المقاييس لـ Prometheus
"""

import time
from typing import Dict
from prometheus_client import start_http_server, Counter, Gauge, Histogram

class MetricsCollector:
    """
    جمع ونشر مقاييس البوت
    """
    
    def __init__(self, bot):
        self.bot = bot
        
        # المقاييس
        self.bot_uptime = Gauge('bot_uptime_seconds', 'Bot uptime in seconds')
        self.total_scans = Counter('bot_total_scans', 'Total opportunity scans')
        self.opportunities_found = Counter('bot_opportunities_found', 'Total opportunities found')
        self.trades_executed = Counter('bot_trades_executed', 'Total trades executed')
        self.trades_successful = Counter('bot_trades_successful', 'Successful trades')
        self.total_profit = Gauge('bot_total_profit', 'Total profit in wei')
        self.total_gas_cost = Gauge('bot_total_gas_cost', 'Total gas cost in wei')
        self.net_profit = Gauge('bot_net_profit', 'Net profit in wei')
        self.active_trades = Gauge('bot_active_trades', 'Number of active trades')
        self.scan_duration = Histogram('bot_scan_duration_seconds', 'Scan duration in seconds')
        self.trade_execution_time = Histogram('bot_trade_execution_time_seconds', 'Trade execution time')
        
        # بدء خادم المقاييس
        start_http_server(8000)
    
    def start_collecting(self):
        """بدء جمع المقاييس"""
        self.is_collecting = True
    
    def update_metrics(self, stats: Dict, active_trades: Dict):
        """تحديث جميع المقاييس"""
        try:
            # الوقت التشغيلي
            uptime = (time.time() - stats['uptime_start'].timestamp())
            self.bot_uptime.set(uptime)
            
            # الإحصائيات
            self.total_scans._value.set(stats.get('total_scans', 0))
            self.opportunities_found._value.set(stats.get('opportunities_found', 0))
            self.trades_executed._value.set(stats.get('trades_executed', 0))
            self.trades_successful._value.set(stats.get('trades_successful', 0))
            
            # الأرباح والتكاليف
            self.total_profit.set(stats.get('total_profit', 0))
            self.total_gas_cost.set(stats.get('total_gas_cost', 0))
            self.net_profit.set(stats.get('total_profit', 0) - stats.get('total_gas_cost', 0))
            
            # الصفقات النشطة
            self.active_trades.set(len(active_trades))
            
        except Exception as e:
            logger.error(f"Error updating metrics: {e}")
    
    def record_scan_duration(self, duration: float):
        """تسجيل مدة المسح"""
        self.scan_duration.observe(duration)
    
    def record_trade_execution(self, duration: float, success: bool):
        """تسجيل تنفيذ الصفقة"""
        self.trade_execution_time.observe(duration)
    
    def stop_collecting(self):
        """إيقاف جمع المقاييس"""
        self.is_collecting = False
