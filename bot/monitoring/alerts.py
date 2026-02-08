"""
نظام الإنذارات
"""

import requests
from typing import Dict

class AlertSystem:
    """
    نظام إرسال الإنذارات
    """
    
    def __init__(self, bot):
        self.bot = bot
        self.config = bot.config.get('alerts', {})
        
        # قنوات الإنذار
        self.telegram_enabled = self.config.get('telegram', {}).get('enabled', False)
        self.discord_enabled = self.config.get('discord', {}).get('enabled', False)
        self.email_enabled = self.config.get('email', {}).get('enabled', False)
        
        if self.telegram_enabled:
            self.telegram_token = self.config['telegram']['token']
            self.telegram_chat_id = self.config['telegram']['chat_id']
    
    async def send_trade_alert(self, trade: Dict):
        """إرسال إنذار بخصوص الصفقة"""
        message = self._format_trade_alert(trade)
        
        if trade['status'] == 'success':
            await self._send_alert(f"✅ {message}", "success")
        else:
            await self._send_alert(f"❌ {message}", "error")
    
    async def send_profit_alert(self, total_profit: int, num_trades: int):
        """إرسال إنذار ربح مرتفع"""
        profit_eth = total_profit / 1e18
        message = f"🚨 HIGH PROFIT ALERT\nTotal profit: {profit_eth:.4f} MATIC\nTrades: {num_trades}"
        
        await self._send_alert(message, "warning")
    
    async def send_failure_alert(self, num_failures: int):
        """إرسال إنذار فشل متتالي"""
        message = f"⚠️ CONSECUTIVE FAILURES\nFailed trades: {num_failures}\nCheck bot immediately!"
        
        await self._send_alert(message, "critical")
    
    async def send_system_alert(self, issue: str):
        """إرسال إنذار نظامي"""
        message = f"🔧 SYSTEM ALERT\nIssue: {issue}\nTime: {datetime.now().isoformat()}"
        
        await self._send_alert(message, "info")
    
    def _format_trade_alert(self, trade: Dict) -> str:
        """تنسيق إنذار الصفقة"""
        if trade['status'] == 'success':
            profit = trade.get('actual_profit', 0) / 1e18
            return (
                f"Trade {trade['id']} SUCCESSFUL\n"
                f"Profit: {profit:.4f} MATIC\n"
                f"Execution time: {trade.get('execution_time', 0):.2f}s\n"
                f"TX: {trade.get('tx_hash', 'N/A')[:20]}..."
            )
        else:
            return (
                f"Trade {trade['id']} FAILED\n"
                f"Error: {trade.get('error', 'Unknown')}\n"
                f"TX: {trade.get('tx_hash', 'N/A')[:20]}..."
            )
    
    async def _send_alert(self, message: str, level: str):
        """إرسال الإنذار عبر جميع القنوات"""
        # Telegram
        if self.telegram_enabled:
            await self._send_telegram_alert(message)
        
        # Discord
        if self.discord_enabled:
            await self._send_discord_alert(message, level)
        
        # Email
        if self.email_enabled:
            await self._send_email_alert(message, level)
        
        # التسجيل المحلي
        logger.info(f"ALERT: {message}")
    
    async def _send_telegram_alert(self, message: str):
        """إرسال إنذار عبر Telegram"""
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                'chat_id': self.telegram_chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            
            response = requests.post(url, json=payload)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Telegram alert failed: {e}")
            return False
    
    async def _send_discord_alert(self, message: str, level: str):
        """إرسال إنذار عبر Discord"""
        try:
            webhook_url = self.config['discord']['webhook_url']
            
            # ألوان مختلفة لمستويات الإنذار
            colors = {
                'success': 0x00ff00,
                'error': 0xff0000,
                'warning': 0xffa500,
                'critical': 0xff0000,
                'info': 0x0080ff
            }
            
            embed = {
                'title': 'Flash Loan Bot Alert',
                'description': message,
                'color': colors.get(level, 0x0080ff),
                'timestamp': datetime.now().isoformat()
            }
            
            payload = {'embeds': [embed]}
            response = requests.post(webhook_url, json=payload)
            return response.status_code == 204
        except Exception as e:
            logger.error(f"Discord alert failed: {e}")
            return False
    
    async def _send_email_alert(self, message: str, level: str):
        """إرسال إنذار عبر البريد الإلكتروني"""
        # يمكن تنفيذ هذا باستخدام SMTP
        pass
