import json
import requests
from typing import Dict, Any, List

def enviar_alerta_slack(webhook_url: str, titulo: Dict[str, Any]) -> bool:
    """Envia um card de alerta interativo no Slack sobre retenção de título."""
    if not webhook_url:
        return False

    tid = titulo.get("titulo_id", "N/A")
    valor = float(titulo.get("valor", 0) or 0)
    fornecedor = titulo.get("fornecedor_id", "N/A")
    motivo = titulo.get("motivo", "Retenção determinística do Guardião")
    
    payload = {
        "text": f"🚨 *Guardião: Retenção de Pagamento Detectada* (Título: {tid})",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🚨 Retenção de Pagamento - R$ {valor:,.2f}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*ID do Título:*\n`{tid}`"},
                    {"type": "mrkdwn", "text": f"*Fornecedor:*\n`{fornecedor}`"},
                    {"type": "mrkdwn", "text": f"*Valor:*\n*R$ {valor:,.2f}*"},
                    {"type": "mrkdwn", "text": f"*Lote Remessa:*\n`{titulo.get('lote_remessa', 'N/A')}`"}
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Motivo da Retenção:*\n>{motivo}"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Liberar Exceção"},
                        "style": "primary",
                        "value": f"liberar_{tid}"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "🚫 Manter Retenção"},
                        "style": "danger",
                        "value": f"manter_{tid}"
                    }
                ]
            },
            {"type": "divider"}
        ]
    }

    try:
        res = requests.post(webhook_url, json=payload, timeout=5)
        return res.status_code == 200
    except Exception as e:
        print(f"[SLACK ERRO] Falha ao enviar para o Slack: {e}")
        return False
