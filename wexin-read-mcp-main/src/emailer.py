"""邮件发送模块"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import markdown

from config import EmailConfig

logger = logging.getLogger(__name__)


class EmailSender:
    """邮件发送器"""

    def __init__(self, config: EmailConfig):
        self.config = config

    def _markdown_to_html(self, md_text: str) -> str:
        """Markdown转HTML邮件正文"""
        html_body = markdown.markdown(
            md_text,
            extensions=["tables", "fenced_code"],
        )
        return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
         line-height: 1.8; color: #333; max-width: 800px; margin: 0 auto; padding: 20px; }}
  h1 {{ color: #c0392b; border-bottom: 2px solid #e74c3c; padding-bottom: 10px; }}
  h2 {{ color: #2c3e50; margin-top: 28px; }}
  h3 {{ color: #34495e; }}
  strong {{ color: #e74c3c; }}
  blockquote {{ border-left: 4px solid #3498db; padding-left: 16px; color: #555;
                background: #f8f9fa; margin: 16px 0; padding: 12px 16px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
  th {{ background: #f2f2f2; }}
  hr {{ border: none; border-top: 1px solid #eee; margin: 24px 0; }}
  .footer {{ color: #999; font-size: 12px; margin-top: 40px; padding-top: 16px; border-top: 1px solid #eee; }}
</style>
</head>
<body>
{html_body}
<div class="footer">
  此报告由「微信文章分析助手」自动生成 | {datetime.now().strftime("%Y-%m-%d %H:%M")}
</div>
</body>
</html>"""

    def send(self, to_email: str, report: str, article_count: int) -> dict:
        """
        发送分析报告邮件

        Args:
            to_email: 收件人邮箱
            report: Markdown格式的分析报告
            article_count: 文章数量

        Returns:
            dict: {"success": bool, "error": str|None}
        """
        if not self.config.smtp_server or not self.config.sender_email:
            return {"success": False, "error": "邮箱未配置，请先设置SMTP信息"}

        today = datetime.now().strftime("%Y-%m-%d")
        subject = f"📊 股票博主文章分析报告 ({today}) - 共{article_count}篇"

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.config.sender_email
            msg["To"] = to_email

            # 纯文本备用
            msg.attach(MIMEText(report, "plain", "utf-8"))
            # HTML正文
            html_content = self._markdown_to_html(report)
            msg.attach(MIMEText(html_content, "html", "utf-8"))

            if self.config.use_ssl:
                server = smtplib.SMTP_SSL(self.config.smtp_server, self.config.smtp_port)
            else:
                server = smtplib.SMTP(self.config.smtp_server, self.config.smtp_port)
                server.starttls()

            server.login(self.config.sender_email, self.config.sender_password)
            server.sendmail(self.config.sender_email, to_email, msg.as_string())
            server.quit()

            logger.info(f"邮件发送成功: {to_email}")
            return {"success": True, "error": None}

        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return {"success": False, "error": f"邮件发送失败: {str(e)}"}
