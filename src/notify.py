"""S002 RateAnomaly - Telegram通知"""
import json
import aiohttp

from src.config import Config
from src.logger import log


async def send_telegram(text: str, parse_mode: str = "HTML"):
    """发送Telegram消息"""
    token = Config.TG_BOT_TOKEN
    chat_id = Config.TG_CHAT_ID
    if not token or not chat_id:
        log.debug("Telegram not configured, skip message: %s", text[:80])
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error("TG send failed: %d %s", resp.status, body[:200])
    except Exception as e:
        log.error("TG send error: %s", str(e))


def format_anomaly_alert(anomaly: dict) -> str:
    """格式化异常告警"""
    return (
        f"🚨 <b>异常检测</b>\n"
        f"币种: {anomaly['symbol']}\n"
        f"尺度: {anomaly['scale']}\n"
        f"速率: {anomaly['decline_rate']:.4f} "
        f"(p{anomaly['rate_pctl']*100:.0f})\n"
        f"幅度: {anomaly['decline_amp']:.4f} "
        f"(p{anomaly['amp_pctl']*100:.0f})\n"
        f"评分: {anomaly['anomaly_score']:.0f}"
    )


def format_position_opened(position: dict) -> str:
    """格式化开仓通知"""
    return (
        f"📈 <b>开仓</b>\n"
        f"币种: {position['symbol']}\n"
        f"方向: {position['side']}\n"
        f"入场: {position['entry_price']:.4f}\n"
        f"名义值: {position['notional']:.2f} U\n"
        f"止损: {position['stop_price']:.4f}"
    )


def format_scan_summary(scan_log: dict) -> str:
    """格式化扫描摘要"""
    return (
        f"📊 <b>扫描完成</b>\n"
        f"扫描: {scan_log['coins_scanned']} 币\n"
        f"异常: {scan_log['anomalies_found']}\n"
        f"确认: {scan_log['confirmed']}\n"
        f"入场: {scan_log['entered']}\n"
        f"耗时: {scan_log['duration_ms']/1000:.1f}s"
    )
