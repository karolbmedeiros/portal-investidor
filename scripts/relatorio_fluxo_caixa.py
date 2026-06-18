"""Gera um PDF de analise de fluxo de caixa mensal para uma usina.

Uso:
    python3 scripts/relatorio_fluxo_caixa.py <usina_id> <ano-mes> [saida.pdf]

Exemplo:
    python3 scripts/relatorio_fluxo_caixa.py 2b71ffc3-3009-4004-b2b1-1947af343fd0 2026-05
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.usina_service import buscar_usina, listar_lancamentos

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER

MESES_PT = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


def _fmt_brl(v: float) -> str:
    s = f"{abs(v):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"-R$ {s}" if v < 0 else f"R$ {s}"


def _categorizar(lancamentos: list) -> dict:
    receitas, despesas, sem_categoria = {}, {}, []
    aplicacoes_fundo = resgates_fundo = rendimento_liquido = 0.0

    for l in lancamentos:
        valor = l["valor"]
        if l.get("_neutro"):
            if valor >= 0:
                resgates_fundo += valor
            else:
                aplicacoes_fundo += valor
            continue
        if l.get("_rendimento"):
            rendimento_liquido += valor
            continue

        cat = l.get("categorias_financeiras") or {}
        nome_cat = cat.get("nome")
        tipo_cat = cat.get("tipo")

        if not nome_cat:
            sem_categoria.append(l)
            continue

        if tipo_cat == "receita" or valor > 0:
            receitas[nome_cat] = receitas.get(nome_cat, 0.0) + valor
        else:
            despesas[nome_cat] = despesas.get(nome_cat, 0.0) + valor

    return {
        "receitas": receitas,
        "despesas": despesas,
        "sem_categoria": sem_categoria,
        "aplicacoes_fundo": aplicacoes_fundo,
        "resgates_fundo": resgates_fundo,
        "rendimento_liquido": rendimento_liquido,
    }


def gerar_relatorio(usina_id: str, ano_mes: str, caminho_saida: str, saldo_anterior: float = 0.0) -> str:
    ano, mes = ano_mes.split("-")
    usina = buscar_usina(usina_id)
    if not usina:
        raise ValueError(f"Usina {usina_id} não encontrada.")

    todos = listar_lancamentos(usina_id)
    do_mes = [l for l in todos if l.get("data_transacao", "").startswith(ano_mes)]
    do_mes.sort(key=lambda l: l["data_transacao"])

    dados = _categorizar(do_mes)

    total_receitas = sum(dados["receitas"].values())
    total_despesas = sum(dados["despesas"].values())
    total_sem_categoria = sum(l["valor"] for l in dados["sem_categoria"])
    resultado_operacional = total_receitas + total_despesas + total_sem_categoria
    net_fundo = dados["aplicacoes_fundo"] + dados["resgates_fundo"]
    rendimento = dados["rendimento_liquido"]
    saldo_final = saldo_anterior + resultado_operacional + rendimento

    nome_usina = usina.get("razao_social") or usina.get("nome_fantasia") or usina_id
    mes_nome = MESES_PT[int(mes)]

    styles = getSampleStyleSheet()
    titulo = ParagraphStyle("titulo", parent=styles["Title"], fontSize=16, spaceAfter=4)
    subtitulo = ParagraphStyle("subtitulo", parent=styles["Normal"], fontSize=11, textColor=colors.grey, spaceAfter=14)
    secao = ParagraphStyle("secao", parent=styles["Heading2"], fontSize=12, spaceBefore=14, spaceAfter=6)
    normal = styles["Normal"]

    doc = SimpleDocTemplate(
        caminho_saida, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        title=f"Fluxo de Caixa - {nome_usina} - {mes_nome}/{ano}",
    )

    elementos = []
    elementos.append(Paragraph("Análise de Fluxo de Caixa", titulo))
    elementos.append(Paragraph(f"{nome_usina} &nbsp;&middot;&nbsp; {mes_nome}/{ano}", subtitulo))

    def tabela_resumo(linhas, destacar_ultima=True):
        t = Table(linhas, colWidths=[11 * cm, 5 * cm])
        estilo = [
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#e0e0e0")),
        ]
        if destacar_ultima:
            estilo += [
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ]
        t.setStyle(TableStyle(estilo))
        return t

    # Resumo geral
    elementos.append(Paragraph("Resumo do Período", secao))
    resumo = [
        ["Saldo anterior (início do mês)", _fmt_brl(saldo_anterior)],
        ["(+) Receitas operacionais", _fmt_brl(total_receitas)],
        ["(-) Despesas operacionais", _fmt_brl(total_despesas + total_sem_categoria)],
        ["(=) Resultado operacional", _fmt_brl(resultado_operacional)],
        ["(+) Rendimento líquido de fundo (IR/IOF deduzidos)", _fmt_brl(rendimento)],
        ["Saldo final (fim do mês)", _fmt_brl(saldo_final)],
    ]
    elementos.append(tabela_resumo(resumo))

    # Receitas
    elementos.append(Paragraph("Receitas por Categoria", secao))
    linhas_receita = [["Categoria", "Valor"]] + [
        [k, _fmt_brl(v)] for k, v in sorted(dados["receitas"].items(), key=lambda x: -x[1])
    ] + [["Total de Receitas", _fmt_brl(total_receitas)]]
    t = Table(linhas_receita, colWidths=[11 * cm, 5 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2e7d32")),
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
    elementos.append(t)

    # Despesas
    elementos.append(Paragraph("Despesas por Categoria", secao))
    linhas_despesa_items = sorted(dados["despesas"].items(), key=lambda x: x[1])
    linhas_despesa = [["Categoria", "Valor"]] + [
        [k, _fmt_brl(v)] for k, v in linhas_despesa_items
    ]
    if dados["sem_categoria"]:
        for l in dados["sem_categoria"]:
            linhas_despesa.append([
                l.get("descricao_original", "")[:40],
                _fmt_brl(l["valor"]),
            ])
    linhas_despesa.append(["Total de Despesas", _fmt_brl(total_despesas + total_sem_categoria)])
    t = Table(linhas_despesa, colWidths=[11 * cm, 5 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#c62828")),
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
    elementos.append(t)

    # Movimentação de fundo automático
    elementos.append(Paragraph("Movimentação do Fundo Automático", secao))
    linhas_fundo = [
        ["Aplicações no fundo (excedente de caixa)", _fmt_brl(dados["aplicacoes_fundo"])],
        ["Resgates do fundo (para cobrir despesas)", _fmt_brl(dados["resgates_fundo"])],
        ["Movimentação líquida", _fmt_brl(net_fundo)],
    ]
    elementos.append(tabela_resumo(linhas_fundo))
    elementos.append(Spacer(1, 4))
    elementos.append(Paragraph(
        "Aplicações e resgates do fundo automático são transferências internas dentro da mesma conta "
        "(não representam receita nem despesa) e por isso não entram no resultado operacional.",
        ParagraphStyle("nota", parent=normal, textColor=colors.grey, fontSize=8.5),
    ))

    # Detalhamento (extrato completo do mês)
    elementos.append(PageBreak())
    elementos.append(Paragraph(f"Detalhamento do Extrato — {mes_nome}/{ano}", secao))
    cab = ["Data", "Histórico", "Valor"]
    linhas_detalhe = [cab]
    for l in do_mes:
        data_fmt = datetime.strptime(l["data_transacao"], "%Y-%m-%d").strftime("%d/%m/%Y")
        desc = l.get("descricao") or l.get("descricao_original") or ""
        linhas_detalhe.append([data_fmt, Paragraph(desc, normal), _fmt_brl(l["valor"])])
    t = Table(linhas_detalhe, colWidths=[2.3 * cm, 10.7 * cm, 3 * cm], repeatRows=1)
    estilo_detalhe = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#37474f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e0e0e0")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for i, l in enumerate(do_mes, start=1):
        if l["valor"] < 0:
            estilo_detalhe.append(("TEXTCOLOR", (2, i), (2, i), colors.HexColor("#c62828")))
        else:
            estilo_detalhe.append(("TEXTCOLOR", (2, i), (2, i), colors.HexColor("#2e7d32")))
        if i % 2 == 0:
            estilo_detalhe.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafafa")))
    t.setStyle(TableStyle(estilo_detalhe))
    elementos.append(t)

    elementos.append(Spacer(1, 16))
    elementos.append(Paragraph(
        f"Relatório gerado automaticamente em {datetime.now().strftime('%d/%m/%Y %H:%M')}.",
        ParagraphStyle("rodape", parent=normal, textColor=colors.grey, fontSize=8),
    ))

    doc.build(elementos)
    return caminho_saida


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    usina_id_arg = sys.argv[1]
    ano_mes_arg = sys.argv[2]
    saida_arg = sys.argv[3] if len(sys.argv) > 3 else f"relatorios/fluxo_caixa_{ano_mes_arg}.pdf"
    os.makedirs(os.path.dirname(saida_arg) or ".", exist_ok=True)
    caminho = gerar_relatorio(usina_id_arg, ano_mes_arg, saida_arg)
    print(f"PDF gerado em: {caminho}")
