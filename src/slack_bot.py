"""Bot do Slack em Socket Mode (SPEC item 6): o humano decide [Reter] ou [Liberar] por título.

Uso (depois de rodar o guardião, que deixa os BLOQUEIA retidos por padrão):
    python -m src.guardiao  --agora 2026-11-02T08:00 --sem-ia --sem-slack
    python -m src.slack_bot --agora 2026-11-02T08:00 [--lote RM20261103-MER]

O bot:
1. recalcula os alertas (determinístico) e separa os títulos BLOQUEIA ainda sem decisão humana;
2. posta UMA mensagem por lote (quebrada em partes de até TITULOS_POR_MENSAGEM títulos),
   com evidência mascarada e os botões [Reter] [Liberar] em cada título;
3. fica conectado por Socket Mode (sem URL pública): cada clique vira uma `Decisao`
   (origem SLACK), é gravado no SQLite e escrito em data/agenda_simulada.csv;
4. se o Slack não estiver configurado ou falhar, manda os pendentes para a fila de triagem
   manual (plano B do kit). Os títulos continuam retidos: o silêncio retém (fail-safe).

Rodar de novo não reposta mensagens já enviadas (idempotência via `acionamentos`).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.carga import Base, carregar, politica
from src.checks import rodar_regras
from src.config import load_settings
from src.executors import AgendaSimuladaExecutor
from src.pii import mask_pii
from src.schemas import Acao, Decisao, OrigemDecisao, Severidade
from src.store import Store

ACTION_RETER = "guardiao_reter"
ACTION_LIBERAR = "guardiao_liberar"
TITULOS_POR_MENSAGEM = 20      # 1 header + 2 blocos por título = 41 blocos (< 50 do Slack)
MAX_EVIDENCIA = 280            # caracteres por evidência no card
DESTINO_CANAL = "canal"        # pessoa_id usado no registro de acionamento (sem roteamento por pessoa)
DB_PADRAO = "data/guardiao.db" # mesmo banco do guardiao.py
ORIGENS_HUMANAS = {OrigemDecisao.SLACK.value, OrigemDecisao.CLI.value}


# --------------------------------------------------------------------- dados (puro)


@dataclass
class Pendente:
    titulo_id: str
    lote_remessa: str
    fornecedor_id: str
    categoria: str
    valor: float
    motivos: List[str] = field(default_factory=list)   # "R1_duplicidade: evidência mascarada"


def _brl(valor: float) -> str:
    inteiro, centavos = f"{abs(valor):,.2f}".split(".")
    return f"{'-' if valor < 0 else ''}R$ {inteiro.replace(',', '.')},{centavos}"


def _mascarar(texto: str) -> str:
    """Evidência para o Slack: PII mascarada e tamanho limitado."""
    limpo = mask_pii(str(texto or "")).text
    return limpo if len(limpo) <= MAX_EVIDENCIA else limpo[: MAX_EVIDENCIA - 1] + "…"


def pendentes(base: Base, alertas: pd.DataFrame, store: Optional[Store] = None) -> List[Pendente]:
    """Títulos da agenda com alerta BLOQUEIA e ainda sem decisão humana (Slack ou CLI)."""
    if alertas is None or alertas.empty:
        return []
    bloq = alertas[alertas["severidade"].astype(str).str.upper() == Severidade.BLOQUEIA.value]
    if bloq.empty:
        return []
    agenda = base.agenda.drop_duplicates("titulo_id").set_index("titulo_id")
    categoria = base.fornecedores.drop_duplicates("fornecedor_id").set_index("fornecedor_id")["categoria"]

    saida: List[Pendente] = []
    for tid, grupo in bloq.groupby("titulo_id", sort=True):
        if tid not in agenda.index:
            continue
        linha = agenda.loc[tid]
        lote = str(linha["lote_remessa"])
        if store is not None:
            ultima = store.ultima_decisao(tid, lote)
            if ultima and ultima.get("origem") in ORIGENS_HUMANAS:
                continue
        forn = str(linha["fornecedor_id"])
        saida.append(Pendente(
            titulo_id=tid, lote_remessa=lote, fornecedor_id=forn,
            categoria=str(categoria.get(forn, "?")), valor=float(linha["valor"]),
            motivos=[f"{r.regra}: {_mascarar(r.evidencia)}" for r in grupo.itertuples(index=False)],
        ))
    return sorted(saida, key=lambda p: (p.lote_remessa, -p.valor, p.titulo_id))


def fechamento_do_lote(base: Base, lote: str) -> str:
    """'03/11 10:00' a partir de lotes_remessa (vazio se não houver o lote)."""
    lotes = getattr(base, "lotes", None)
    if lotes is None or lotes.empty or "lote_remessa" not in lotes:
        return ""
    linha = lotes[lotes["lote_remessa"] == lote]
    if linha.empty:
        return ""
    data = pd.to_datetime(linha.iloc[0].get("data_pagamento"), errors="coerce")
    hora = str(linha.iloc[0].get("horario_fechamento") or "").strip()
    return f"{data:%d/%m} {hora}".strip() if pd.notna(data) else hora


# --------------------------------------------------------------------- mensagens (puro)


def _bloco_titulo(p: Pendente) -> List[Dict[str, Any]]:
    motivos = "\n".join(f"• {m}" for m in p.motivos)
    valor_botao = f"{p.titulo_id}|{p.lote_remessa}"
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            f"*{p.titulo_id}* · {p.fornecedor_id} ({p.categoria}) · *{_brl(p.valor)}*\n{motivos}"
        )[:2900]}},
        {"type": "actions", "block_id": f"dec|{valor_botao}", "elements": [
            {"type": "button", "action_id": ACTION_RETER, "style": "danger", "value": valor_botao,
             "text": {"type": "plain_text", "text": "Reter"}},
            {"type": "button", "action_id": ACTION_LIBERAR, "style": "primary", "value": valor_botao,
             "text": {"type": "plain_text", "text": "Liberar"}},
        ]},
    ]


def montar_mensagens(lote: str, itens: List[Pendente], fechamento: str = "") -> List[Dict[str, Any]]:
    """Mensagens do lote: cada uma com `text` (notificação) e `blocks` (cards com botões)."""
    mensagens = []
    partes = [itens[i:i + TITULOS_POR_MENSAGEM] for i in range(0, len(itens), TITULOS_POR_MENSAGEM)]
    total = sum(p.valor for p in itens)
    for n, parte in enumerate(partes, start=1):
        sufixo = f" ({n}/{len(partes)})" if len(partes) > 1 else ""
        fecha = f" · fecha {fechamento}" if fechamento else ""
        titulo = f"🛡️ Lote {lote}{sufixo}: {len(itens)} títulos retidos aguardando decisão{fecha}"
        blocos: List[Dict[str, Any]] = [
            {"type": "section", "text": {"type": "mrkdwn", "text": (
                f"*{titulo}*\nTotal retido no lote: *{_brl(total)}*. "
                "Sem resposta até o fechamento, o título continua retido."
            )}},
        ]
        for p in parte:
            blocos.extend(_bloco_titulo(p))
        mensagens.append({"text": titulo, "blocks": blocos})
    return mensagens


def registrar_clique(blocks: List[Dict[str, Any]], block_id: str, texto: str) -> List[Dict[str, Any]]:
    """Troca os botões do título clicado por uma linha com a decisão (os outros seguem iguais)."""
    novos = []
    for b in blocks:
        if b.get("type") == "actions" and b.get("block_id") == block_id:
            novos.append({"type": "context", "elements": [{"type": "mrkdwn", "text": texto}]})
        else:
            novos.append(b)
    return novos


# --------------------------------------------------------------------- decisão (efeito)


def aplicar_decisao(store: Store, executor: AgendaSimuladaExecutor, titulo_id: str, lote: str,
                    acao: str, usuario: str, ts_simulado: str) -> Dict[str, Any]:
    """Grava a decisão humana e aplica na agenda simulada. Clique repetido não duplica."""
    acao_enum = Acao.RETER_TITULO if acao == ACTION_RETER else Acao.LIBERAR_TITULO
    palavra = "retido" if acao_enum is Acao.RETER_TITULO else "liberado"
    decisao = Decisao(titulo_id=titulo_id, lote_remessa=lote, acao=acao_enum,
                      decidido_por=str(usuario)[:80] or "slack", origem=OrigemDecisao.SLACK,
                      motivo=f"decisão humana no Slack: {palavra}")
    decisao_id, criada = store.record_decisao(decisao, ts_simulado)
    if not criada:
        return {"status": "DUPLICADA", "palavra": palavra, "detalhe": "decisão já registrada"}
    resultado = executor.execute(decisao_id, decisao, ts_simulado)
    store.set_execution(decisao_id, resultado.status, resultado.detail)
    return {"status": resultado.status, "palavra": palavra, "detalhe": resultado.detail}


def enviar_para_triagem(store: Store, itens: List[Pendente], motivo: str) -> int:
    """Plano B: pendentes vão para a fila manual (continuam retidos na agenda)."""
    novos = 0
    for p in itens:
        _, criado = store.enqueue_manual_triage(
            f"{p.titulo_id}|{p.lote_remessa}", motivo, "; ".join(p.motivos)[:1000])
        novos += int(criado)
    return novos


# --------------------------------------------------------------------- execução


def _preparar(dados_dir: str, db: str):
    settings = load_settings()
    base = carregar(dados_dir)
    pol = politica(dados_dir)
    pol.setdefault("guardiao", {})["dias_lancador_recente"] = settings.dias_lancador_recente
    alertas = rodar_regras(base.agenda, base, pol)
    store = Store(db)
    executor = AgendaSimuladaExecutor(str(Path(dados_dir) / "agenda_remessa.csv"),
                                      str(Path("data") / "agenda_simulada.csv"))
    return settings, base, store, executor, pendentes(base, alertas, store)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Guardião: decisões humanas pelo Slack (Socket Mode)")
    parser.add_argument("--agora", default=None, help="relógio simulado, ex.: 2026-11-02T08:00")
    parser.add_argument("--db", default=DB_PADRAO)
    parser.add_argument("--lote", default=None, help="manda só este lote (ex.: RM20261103-MER)")
    args = parser.parse_args(argv)

    settings = load_settings()
    agora = args.agora or settings.agora
    settings, base, store, executor, itens = _preparar(settings.dados_dir, args.db)
    if args.lote:
        itens = [p for p in itens if p.lote_remessa == args.lote]
    print(f"[SLACK] {len(itens)} títulos BLOQUEIA aguardando decisão humana.")

    if not settings.slack_enabled:
        n = enviar_para_triagem(store, itens, "slack_nao_configurado")
        print("[SLACK] Faltam SLACK_BOT_TOKEN, SLACK_APP_TOKEN ou SLACK_CANAL no .env.")
        print(f"[SLACK] Plano B: {n} títulos novos na fila de triagem manual (seguem retidos).")
        return 1

    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    from slack_bolt.error import BoltError
    from slack_sdk.errors import SlackApiError

    if not settings.slack_bot_token.startswith("xoxb-") or not settings.slack_app_token.startswith("xapp-"):
        print("[SLACK ERRO] SLACK_BOT_TOKEN deve começar com xoxb- e SLACK_APP_TOKEN com xapp- (confira o .env).")
        n = enviar_para_triagem(store, itens, "slack_token_formato")
        print(f"[SLACK] Plano B: {n} títulos novos na fila de triagem manual (seguem retidos).")
        return 1
    try:
        app = App(token=settings.slack_bot_token)
    except OSError as erro:  # sem internet / DNS / proxy
        print(f"[SLACK ERRO] Sem conexão com o Slack ({erro}).")
        n = enviar_para_triagem(store, itens, "slack_sem_conexao")
        print(f"[SLACK] Plano B: {n} títulos novos na fila de triagem manual (seguem retidos).")
        return 1
    except (BoltError, SlackApiError) as erro:
        print(f"[SLACK ERRO] O Slack recusou o SLACK_BOT_TOKEN ({erro}).")
        print("[SLACK] Pegue o Bot User OAuth Token atual em api.slack.com/apps > seu app > Install App "
              "(reinstale se tiver revogado) e atualize o .env.")
        n = enviar_para_triagem(store, itens, "slack_token_invalido")
        print(f"[SLACK] Plano B: {n} títulos novos na fila de triagem manual (seguem retidos).")
        return 1

    def _ao_clicar(ack, body, client, action):
        ack()
        titulo_id, lote = action["value"].split("|", 1)
        usuario = body.get("user", {})
        nome = usuario.get("username") or usuario.get("name") or usuario.get("id", "slack")
        r = aplicar_decisao(store, executor, titulo_id, lote, action["action_id"], nome, agora)
        icone = "🛑" if r["palavra"] == "retido" else "✅"
        texto = f"{icone} *{titulo_id}* {r['palavra']} por <@{usuario.get('id', '')}>"
        if r["status"] not in ("WRITTEN", "DUPLICADA"):
            texto += f" · ⚠️ {r['detalhe']}"
        msg = body.get("message", {})
        client.chat_update(channel=body["channel"]["id"], ts=msg["ts"], text=msg.get("text", ""),
                           blocks=registrar_clique(msg.get("blocks", []), action["block_id"], texto))
        print(f"[SLACK] {titulo_id} {r['palavra']} por {nome} ({r['status']})")

    app.action(ACTION_RETER)(_ao_clicar)
    app.action(ACTION_LIBERAR)(_ao_clicar)

    por_lote: Dict[str, List[Pendente]] = {}
    for p in itens:
        por_lote.setdefault(p.lote_remessa, []).append(p)
    try:
        for lote, grupo in por_lote.items():
            for msg in montar_mensagens(lote, grupo, fechamento_do_lote(base, lote)):
                titulos = [b["block_id"].split("|")[1] for b in msg["blocks"] if b.get("type") == "actions"]
                _, novo = store.record_acionamento(DESTINO_CANAL, lote, titulos, "slack",
                                                   msg["text"], agora)
                if novo:
                    app.client.chat_postMessage(channel=settings.slack_canal, text=msg["text"],
                                                blocks=msg["blocks"])
                    print(f"[SLACK] Enviado: {msg['text']}")
                else:
                    print(f"[SLACK] Já enviado antes (não repito): {msg['text']}")
    except OSError as erro:
        print(f"[SLACK ERRO] Sem conexão com o Slack ({erro}).")
        n = enviar_para_triagem(store, itens, "slack_sem_conexao")
        print(f"[SLACK] Plano B: {n} títulos novos na fila de triagem manual (seguem retidos).")
        return 1
    except SlackApiError as erro:
        codigo = erro.response.get("error", "?")
        dica = {"not_in_channel": "convide o bot no canal com /invite @nome-do-app",
                "channel_not_found": "confira SLACK_CANAL (use o ID do canal, ex.: C0123...)",
                "invalid_auth": "SLACK_BOT_TOKEN inválido (deve começar com xoxb-)",
                "missing_scope": "falta o escopo chat:write no app; reinstale o app depois"}.get(codigo, "")
        print(f"[SLACK ERRO] {codigo}. {dica}")
        n = enviar_para_triagem(store, itens, f"slack_erro_{codigo}")
        print(f"[SLACK] Plano B: {n} títulos novos na fila de triagem manual (seguem retidos).")
        return 1

    print("[SLACK] Conectado em Socket Mode. Clique nos botões no canal. Ctrl+C para encerrar.")
    SocketModeHandler(app, settings.slack_app_token).start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
