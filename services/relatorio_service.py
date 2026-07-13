"""Geração de relatório PDF resumido por usina (admin)."""
import io
from datetime import date, datetime, timedelta

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from services.usina_service import (
    buscar_usina, clientes_da_usina, leituras_detalhadas,
    saldo_creditos_da_usina, financiamentos_da_usina,
    contas_pagar_da_usina, documentos_da_usina,
    listar_contas_da_usina, listar_lancamentos, calcular_saldo_usina_em,
)
from services.supabase_client import get_service_client

MESES_PT = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


def _fmt_brl(v: float) -> str:
    s = f"{abs(v or 0):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"-R$ {s}" if (v or 0) < 0 else f"R$ {s}"


def _fmt_data(v) -> str:
    if not v:
        return "—"
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return str(v)


def _fmt_kwh(v: float) -> str:
    s = f"{abs(v or 0):,.0f}".replace(",", ".")
    return f"{s} kWh"


def _dados_fluxo_caixa(usina_id: str, ref_mes: str) -> dict:
    ano, mes = (int(x) for x in ref_mes.split("-"))
    ultimo_dia_mes_anterior = date(ano, mes, 1) - timedelta(days=1)
    hoje = date.today()

    saldo_inicio = calcular_saldo_usina_em(usina_id, ultimo_dia_mes_anterior.isoformat())
    saldo_atual = calcular_saldo_usina_em(usina_id, hoje.isoformat())

    entradas: dict = {}
    saidas: dict = {}
    for conta in listar_contas_da_usina(usina_id):
        for l in listar_lancamentos(usina_id, conta_id=conta["id"]):
            data = l.get("data_transacao")
            if not data or data[:7] != ref_mes or l.get("_neutro"):
                continue
            cat = l.get("categorias_financeiras") or {}
            nome_cat = cat.get("nome") or "Sem categoria"
            v = abs(l.get("valor") or 0)
            if l.get("tipo") == "credito":
                entradas[nome_cat] = entradas.get(nome_cat, 0.0) + v
            else:
                saidas[nome_cat] = saidas.get(nome_cat, 0.0) + v

    contas_pagar = contas_pagar_da_usina(usina_id, ref_mes)

    return {
        "saldo_inicio": saldo_inicio,
        "saldo_atual": saldo_atual,
        "variacao": round(saldo_atual - saldo_inicio, 2),
        "entradas": entradas,
        "saidas": saidas,
        "total_entradas": round(sum(entradas.values()), 2),
        "total_saidas": round(sum(saidas.values()), 2),
        "nao_pago_total": contas_pagar["aberto"],
        "nao_pago_qtd": contas_pagar["n_total"] - contas_pagar["n_pagas"],
    }


def _dados_energia_clientes(usina_id: str, ref_mes: str) -> dict:
    clientes = clientes_da_usina(usina_id)
    contratos_ativos = sum(1 for c in clientes if c.get("status") == "Ativo")

    leituras = leituras_detalhadas(usina_id)
    do_mes = [l for l in leituras if (l.get("ref_mes_ano") or "")[:7] == ref_mes]
    kwh_compensado = sum(float(l.get("kwh_compensado") or 0) for l in do_mes)
    kwh_consumido = sum(float(l.get("kwh_consumido") or 0) for l in do_mes)

    ano, mes = (int(x) for x in ref_mes.split("-"))
    prox_ano, prox_mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    ref_ini = f"{ref_mes}-01"
    ref_fim = f"{prox_ano:04d}-{prox_mes:02d}-01"
    sb = get_service_client()
    faturas = (
        sb.from_("v_faturas_completas")
        .select("id,status")
        .eq("usina_id", usina_id)
        .gte("ref_mes_ano", ref_ini)
        .lt("ref_mes_ano", ref_fim)
        .execute()
    ).data or []
    emitidas = len(faturas)
    pagas = sum(1 for f in faturas if f.get("status") == "Paga")
    abertas = emitidas - pagas

    saldo_creditos = sum(float(s.get("saldo_atual_kwh") or 0) for s in saldo_creditos_da_usina(usina_id))

    return {
        "contratos_ativos": contratos_ativos,
        "kwh_compensado": kwh_compensado,
        "kwh_consumido": kwh_consumido,
        "faturas_emitidas": emitidas,
        "faturas_pagas": pagas,
        "faturas_abertas": abertas,
        "saldo_creditos": saldo_creditos,
    }


def _dados_financiamento_contas(usina_id: str) -> dict:
    financiamentos = [f for f in financiamentos_da_usina(usina_id) if f.get("ativo")]
    saldo_devedor = sum(f.get("saldo_devedor") or 0 for f in financiamentos)
    proxima_parcela = None
    for f in financiamentos:
        p = f.get("proxima_parcela")
        if p and (proxima_parcela is None or (p.get("data_vencimento") or "") < (proxima_parcela.get("data_vencimento") or "")):
            proxima_parcela = p

    contas_pagar = contas_pagar_da_usina(usina_id)
    vencidas = [c for c in contas_pagar["itens"] if c.get("atrasada")]
    valor_vencidas = sum(c.get("valor") or 0 for c in vencidas)

    return {
        "tem_financiamento": bool(financiamentos),
        "saldo_devedor": saldo_devedor,
        "proxima_parcela": proxima_parcela,
        "contas_aberto": contas_pagar["aberto"],
        "n_vencidas": len(vencidas),
        "valor_vencidas": valor_vencidas,
    }


def gerar_relatorio_usina_pdf(usina_id: str) -> io.BytesIO:
    usina = buscar_usina(usina_id)
    if not usina:
        raise ValueError(f"Usina {usina_id} não encontrada.")

    ref_mes = date.today().strftime("%Y-%m")
    mes_nome = MESES_PT[date.today().month]
    ano = date.today().year

    ultimo_dia_mes_anterior = date(ano, date.today().month, 1) - timedelta(days=1)
    ref_mes_anterior = ultimo_dia_mes_anterior.strftime("%Y-%m")
    mes_nome_anterior = MESES_PT[ultimo_dia_mes_anterior.month]
    ano_mes_anterior = ultimo_dia_mes_anterior.year

    fluxo_caixa = _dados_fluxo_caixa(usina_id, ref_mes)
    energia = _dados_energia_clientes(usina_id, ref_mes_anterior)
    fin_contas = _dados_financiamento_contas(usina_id)
    documentos = documentos_da_usina(usina_id)

    styles = getSampleStyleSheet()
    titulo = ParagraphStyle("titulo", parent=styles["Title"], fontSize=16, spaceAfter=4)
    subtitulo = ParagraphStyle("subtitulo", parent=styles["Normal"], fontSize=11, textColor=colors.grey, spaceAfter=14)
    secao = ParagraphStyle("secao", parent=styles["Heading2"], fontSize=12, spaceBefore=14, spaceAfter=6)
    normal = styles["Normal"]

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        title=f"Relatório - {usina['nome']} - {mes_nome}/{ano}",
    )

    def tabela_resumo(linhas):
        t = Table(linhas, colWidths=[11 * cm, 5 * cm])
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#e0e0e0")),
        ]))
        return t

    def tabela_categorias(titulo_col: str, valores: dict, total: float, cor_header: str):
        linhas = [[titulo_col, "Valor"]] + [
            [k, _fmt_brl(v)] for k, v in sorted(valores.items(), key=lambda x: -x[1])
        ] + [["Total", _fmt_brl(total)]]
        t = Table(linhas, colWidths=[11 * cm, 5 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(cor_header)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#e0e0e0")),
            ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return t

    elementos = []
    elementos.append(Paragraph("Relatório de Usina", titulo))
    elementos.append(Paragraph(f"{usina['nome']} &nbsp;&middot;&nbsp; {mes_nome}/{ano}", subtitulo))

    # 1. Cabeçalho
    elementos.append(Paragraph("Dados Cadastrais", secao))
    elementos.append(tabela_resumo([
        ["CNPJ", usina.get("cnpj") or "—"],
        ["Status", usina.get("status") or "—"],
        ["Potência instalada", f"{usina.get('potencia_instalada_kwp') or 0} kWp"],
        ["Modalidade GD", usina.get("modalidade_gd") or "—"],
        ["Período de referência", f"{mes_nome}/{ano}"],
    ]))

    # 2. Fluxo de Caixa do Mês
    elementos.append(Paragraph("Fluxo de Caixa do Mês", secao))
    cor_variacao = "#2e7d32" if fluxo_caixa["variacao"] >= 0 else "#c62828"
    elementos.append(tabela_resumo([
        ["Saldo no início do mês", _fmt_brl(fluxo_caixa["saldo_inicio"])],
        ["(+) Entradas do mês", _fmt_brl(fluxo_caixa["total_entradas"])],
        ["(-) Saídas do mês", _fmt_brl(fluxo_caixa["total_saidas"])],
        ["(=) Saldo atual", _fmt_brl(fluxo_caixa["saldo_atual"])],
        [
            "Variação do saldo no mês",
            Paragraph(
                _fmt_brl(fluxo_caixa["variacao"]),
                ParagraphStyle("variacao", parent=normal, textColor=colors.HexColor(cor_variacao), fontSize=9.5, alignment=2, fontName="Helvetica-Bold"),
            ),
        ],
        [
            "Ainda não pago (contas em aberto no mês)",
            f"{_fmt_brl(fluxo_caixa['nao_pago_total'])} ({fluxo_caixa['nao_pago_qtd']} conta(s))",
        ],
    ]))
    if fluxo_caixa["entradas"]:
        elementos.append(Spacer(1, 8))
        elementos.append(tabela_categorias("Entradas por Categoria", fluxo_caixa["entradas"], fluxo_caixa["total_entradas"], "#2e7d32"))
    if fluxo_caixa["saidas"]:
        elementos.append(Spacer(1, 8))
        elementos.append(tabela_categorias("Saídas por Categoria", fluxo_caixa["saidas"], fluxo_caixa["total_saidas"], "#c62828"))

    # 3. Energia e clientes
    elementos.append(Paragraph(f"Energia e Clientes ({mes_nome_anterior}/{ano_mes_anterior})", secao))
    elementos.append(tabela_resumo([
        ["Contratos ativos", str(energia["contratos_ativos"])],
        ["Energia compensada no mês", _fmt_kwh(energia["kwh_compensado"])],
        ["Energia consumida no mês", _fmt_kwh(energia["kwh_consumido"])],
        ["Faturas emitidas", str(energia["faturas_emitidas"])],
        ["Faturas pagas", str(energia["faturas_pagas"])],
        ["Faturas em aberto", str(energia["faturas_abertas"])],
        ["Saldo de créditos acumulado", _fmt_kwh(energia["saldo_creditos"])],
    ]))

    # 4. Financiamento e contas a pagar
    elementos.append(Paragraph("Financiamento e Contas a Pagar", secao))
    linhas_fin = []
    if fin_contas["tem_financiamento"]:
        linhas_fin.append(["Saldo devedor do financiamento", _fmt_brl(fin_contas["saldo_devedor"])])
        prox = fin_contas["proxima_parcela"]
        if prox:
            linhas_fin.append([
                "Próxima parcela",
                f"{_fmt_brl((prox.get('valor_principal') or 0) + (prox.get('valor_juros') or 0))} em {_fmt_data(prox.get('data_vencimento'))}",
            ])
        else:
            linhas_fin.append(["Próxima parcela", "Nenhuma parcela em aberto"])
    else:
        linhas_fin.append(["Financiamento", "Sem financiamento ativo"])
    linhas_fin.append(["Contas a pagar em aberto", _fmt_brl(fin_contas["contas_aberto"])])
    cor_vencidas = "#c62828" if fin_contas["n_vencidas"] else "#2e2e2e"
    linhas_fin.append([
        "Contas vencidas",
        Paragraph(
            f"{fin_contas['n_vencidas']} conta(s) &middot; {_fmt_brl(fin_contas['valor_vencidas'])}",
            ParagraphStyle("vencidas", parent=normal, textColor=colors.HexColor(cor_vencidas), fontSize=9.5, alignment=2),
        ),
    ])
    elementos.append(tabela_resumo(linhas_fin))

    # 5. Documentos
    elementos.append(Paragraph("Documentos Anexados", secao))
    if documentos:
        linhas_doc = [["Nome", "Data de upload"]] + [
            [d.get("nome") or "—", _fmt_data(d.get("created_at"))] for d in documentos
        ]
        t = Table(linhas_doc, colWidths=[11 * cm, 5 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#37474f")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e0e0e0")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elementos.append(t)
    else:
        elementos.append(Paragraph("Nenhum documento anexado.", normal))

    elementos.append(Spacer(1, 16))
    elementos.append(Paragraph(
        f"Relatório gerado automaticamente em {datetime.now().strftime('%d/%m/%Y %H:%M')}.",
        ParagraphStyle("rodape", parent=normal, textColor=colors.grey, fontSize=8),
    ))

    doc.build(elementos)
    buffer.seek(0)
    return buffer
