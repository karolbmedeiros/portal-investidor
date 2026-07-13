"""Geração de relatório PDF resumido por usina (admin)."""
import io
from datetime import date, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from services.usina_service import (
    buscar_usina, clientes_da_usina, leituras_detalhadas,
    saldo_creditos_da_usina, financiamentos_da_usina,
    contas_pagar_da_usina, documentos_da_usina,
)
from services.dre_service import listar_secoes_dre, calcular_dre
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


def _dados_financeiro(usina_id: str, ref_mes: str) -> dict:
    secoes = listar_secoes_dre()
    dre = calcular_dre(usina_id, ref_mes, ref_mes, secoes=secoes)
    valores = dre["valores"]

    def _secao_id(nome: str):
        s = next((s for s in secoes if (s.get("nome") or "").strip().upper() == nome), None)
        return s["id"] if s else None

    id_receita = _secao_id("RECEITA OPERACIONAL BRUTA")
    id_lucro = _secao_id("LUCRO LÍQUIDO DO EXERCÍCIO")

    receita_total = valores.get(id_receita, {}).get(ref_mes, 0.0) if id_receita else 0.0
    resultado_liquido = valores.get(id_lucro, {}).get(ref_mes, 0.0) if id_lucro else 0.0
    despesas_totais = receita_total - resultado_liquido
    margem = (resultado_liquido / receita_total * 100) if receita_total else 0.0

    return {
        "receita_total": receita_total,
        "despesas_totais": despesas_totais,
        "resultado_liquido": resultado_liquido,
        "margem": margem,
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

    financeiro = _dados_financeiro(usina_id, ref_mes)
    energia = _dados_energia_clientes(usina_id, ref_mes)
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

    # 2. Financeiro
    elementos.append(Paragraph("Financeiro (DRE do mês)", secao))
    elementos.append(tabela_resumo([
        ["Receita total", _fmt_brl(financeiro["receita_total"])],
        ["Despesas totais", _fmt_brl(financeiro["despesas_totais"])],
        ["Resultado líquido", _fmt_brl(financeiro["resultado_liquido"])],
        ["Margem líquida", f"{financeiro['margem']:.1f}%"],
    ]))

    # 3. Energia e clientes
    elementos.append(Paragraph("Energia e Clientes", secao))
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
