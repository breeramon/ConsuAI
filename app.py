"""
Controle de Gastos Financeiros - app simples com Flask + SQLite
Uso pessoal (single user por conta), pensado para rodar localmente.
"""

import os
import re
import sqlite3
import uuid
from datetime import datetime

from flask import Flask, g, redirect, render_template, request, session, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "financas.db")

app = Flask(__name__)
app.secret_key = "troque-esta-chave-se-quiser-mais-seguranca"

MESES_PT = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

CATEGORIAS_RECEITA = ["Salário", "Outros Ganhos"]
CATEGORIAS_DESPESA = [
    "Comida", "Despesas Fixas", "Transporte",
    "Entretenimento", "Saúde", "Férias", "Outros",
]
TODAS_CATEGORIAS = CATEGORIAS_RECEITA + CATEGORIAS_DESPESA

# Despesas fixas (recorrentes) são geradas em blocos de meses, e o bloco é
# renovado automaticamente conforme o usuário navega para meses futuros.
JANELA_FIXO_INICIAL = 24
JANELA_FIXO_EXTENSAO = 24
MARGEM_FIXO_MESES = 12


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salario_padrao REAL NOT NULL DEFAULT 0
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS lancamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tipo TEXT NOT NULL CHECK(tipo IN ('receita', 'despesa')),
            categoria TEXT NOT NULL,
            descricao TEXT,
            valor REAL NOT NULL,
            ano INTEGER NOT NULL,
            mes INTEGER NOT NULL,
            criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
            parcela_atual INTEGER,
            parcela_total INTEGER,
            parcela_grupo TEXT,
            fixo_grupo TEXT,
            fixo_ativo INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )

    # Migração simples: garante que bancos criados antes dos recursos de
    # parcelamento/despesa fixa ganhem as colunas novas sem perder os dados existentes.
    colunas_existentes = {
        linha["name"] for linha in db.execute("PRAGMA table_info(lancamentos)")
    }
    for coluna in ("parcela_atual", "parcela_total"):
        if coluna not in colunas_existentes:
            db.execute(f"ALTER TABLE lancamentos ADD COLUMN {coluna} INTEGER")
    if "parcela_grupo" not in colunas_existentes:
        db.execute("ALTER TABLE lancamentos ADD COLUMN parcela_grupo TEXT")
    if "fixo_grupo" not in colunas_existentes:
        db.execute("ALTER TABLE lancamentos ADD COLUMN fixo_grupo TEXT")
    if "fixo_ativo" not in colunas_existentes:
        db.execute("ALTER TABLE lancamentos ADD COLUMN fixo_ativo INTEGER DEFAULT 1")

    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

def usuario_logado():
    return session.get("user_id")


@app.before_request
def exigir_login():
    rotas_livres = {"login", "registro", "static"}
    if request.endpoint not in rotas_livres and not usuario_logado():
        return redirect(url_for("login"))


@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        username = request.form["username"].strip()
        senha = request.form["senha"]
        confirmar = request.form["confirmar_senha"]
        salario_padrao = request.form.get("salario_padrao", "0").replace(",", ".") or "0"

        if not username or not senha:
            flash("Preencha usuário e senha.", "erro")
            return render_template("registro.html")
        if senha != confirmar:
            flash("As senhas não conferem.", "erro")
            return render_template("registro.html")

        db = get_db()
        existente = db.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existente:
            flash("Esse usuário já existe.", "erro")
            return render_template("registro.html")

        db.execute(
            "INSERT INTO users (username, password_hash, salario_padrao) VALUES (?, ?, ?)",
            (username, generate_password_hash(senha), float(salario_padrao)),
        )
        db.commit()
        flash("Conta criada! Faça login.", "sucesso")
        return redirect(url_for("login"))

    return render_template("registro.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        senha = request.form["senha"]
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if user and check_password_hash(user["password_hash"], senha):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))
        flash("Usuário ou senha inválidos.", "erro")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Helpers de resumo financeiro
# ---------------------------------------------------------------------------

def resumo_do_mes(db, user_id, ano, mes):
    receitas = db.execute(
        """SELECT COALESCE(SUM(valor), 0) AS total FROM lancamentos
           WHERE user_id = ? AND tipo = 'receita' AND ano = ? AND mes = ?""",
        (user_id, ano, mes),
    ).fetchone()["total"]

    despesas_total = db.execute(
        """SELECT COALESCE(SUM(valor), 0) AS total FROM lancamentos
           WHERE user_id = ? AND tipo = 'despesa' AND ano = ? AND mes = ?""",
        (user_id, ano, mes),
    ).fetchone()["total"]

    categorias_totais = db.execute(
        """SELECT categoria, COALESCE(SUM(valor), 0) AS total FROM lancamentos
           WHERE user_id = ? AND tipo = 'despesa' AND ano = ? AND mes = ?
           GROUP BY categoria ORDER BY total DESC""",
        (user_id, ano, mes),
    ).fetchall()

    itens_despesas = db.execute(
        """SELECT id, categoria, descricao, valor, fixo_grupo FROM lancamentos
           WHERE user_id = ? AND tipo = 'despesa' AND ano = ? AND mes = ?
           ORDER BY valor DESC""",
        (user_id, ano, mes),
    ).fetchall()

    despesas_por_categoria = []
    for linha in categorias_totais:
        itens = [i for i in itens_despesas if i["categoria"] == linha["categoria"]]
        despesas_por_categoria.append(
            {"categoria": linha["categoria"], "total": linha["total"], "itens": itens}
        )

    guardado = receitas - despesas_total

    return {
        "receitas": receitas,
        "despesas_total": despesas_total,
        "despesas_por_categoria": despesas_por_categoria,
        "guardado": guardado,
    }


def buscar_lancamentos(db, user_id, ano, mes, categoria=None, descricao=None, valor_min=None, valor_max=None):
    """Busca os lançamentos de um mês, com filtros opcionais de categoria,
    texto da descrição e faixa de valor. Usado só para a lista de
    lançamentos do mês (não afeta os totais do resumo)."""
    condicoes = ["user_id = ?", "ano = ?", "mes = ?"]
    parametros = [user_id, ano, mes]

    if categoria:
        condicoes.append("categoria = ?")
        parametros.append(categoria)

    if descricao:
        condicoes.append("descricao LIKE ?")
        parametros.append(f"%{descricao}%")

    if valor_min is not None:
        condicoes.append("valor >= ?")
        parametros.append(valor_min)

    if valor_max is not None:
        condicoes.append("valor <= ?")
        parametros.append(valor_max)

    query = f"""SELECT * FROM lancamentos WHERE {' AND '.join(condicoes)} ORDER BY valor DESC"""
    return db.execute(query, parametros).fetchall()


def resumo_parcelas_abertas(db, user_id, ano, mes):
    """Resumo apenas dos lançamentos parcelados (ignora despesas fixas
    recorrentes e lançamentos únicos) de um mês específico."""
    itens = db.execute(
        """SELECT * FROM lancamentos
           WHERE user_id = ? AND ano = ? AND mes = ? AND tipo = 'despesa'
             AND parcela_grupo IS NOT NULL
           ORDER BY categoria, valor DESC""",
        (user_id, ano, mes),
    ).fetchall()

    total = sum(item["valor"] for item in itens)

    categorias_map = {}
    for item in itens:
        cat = item["categoria"]
        if cat not in categorias_map:
            categorias_map[cat] = {"categoria": cat, "total": 0.0, "itens": []}
        categorias_map[cat]["total"] += item["valor"]
        categorias_map[cat]["itens"].append(item)

    categorias = sorted(categorias_map.values(), key=lambda c: c["total"], reverse=True)

    return {"total": total, "categorias": categorias}


def resumo_todos_os_meses(db, user_id, ano_atual, mes_atual):
    """Resumo mês a mês, limitado até o mês atual. Meses futuros só existem
    no banco por causa das parcelas e despesas fixas já geradas com
    antecedência, mas não fazem sentido aparecer aqui antes de acontecerem."""
    limite = ano_atual * 12 + mes_atual
    linhas = db.execute(
        """SELECT ano, mes,
                  COALESCE(SUM(CASE WHEN tipo = 'receita' THEN valor ELSE 0 END), 0) AS receitas,
                  COALESCE(SUM(CASE WHEN tipo = 'despesa' THEN valor ELSE 0 END), 0) AS despesas
           FROM lancamentos
           WHERE user_id = ? AND (ano * 12 + mes) <= ?
           GROUP BY ano, mes
           ORDER BY ano DESC, mes DESC""",
        (user_id, limite),
    ).fetchall()

    meses = []
    total_guardado_geral = 0
    for linha in linhas:
        guardado = linha["receitas"] - linha["despesas"]
        total_guardado_geral += guardado
        meses.append(
            {
                "ano": linha["ano"],
                "mes": linha["mes"],
                "nome_mes": MESES_PT[linha["mes"]],
                "receitas": linha["receitas"],
                "despesas": linha["despesas"],
                "guardado": guardado,
            }
        )
    return meses, total_guardado_geral


# ---------------------------------------------------------------------------
# Rotas principais
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    db = get_db()
    hoje = datetime.now()
    ano = int(request.args.get("ano", hoje.year))
    mes = int(request.args.get("mes", hoje.month))

    user = db.execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()

    estender_fixos_se_necessario(db, session["user_id"], ano, mes)
    resumo = resumo_do_mes(db, session["user_id"], ano, mes)

    # Filtros da lista de lançamentos (não afetam os totais do resumo acima).
    filtro_categoria = request.args.get("categoria_filtro", "").strip()
    filtro_descricao = request.args.get("descricao_filtro", "").strip()
    filtro_valor_min_raw = request.args.get("valor_min", "").strip()
    filtro_valor_max_raw = request.args.get("valor_max", "").strip()

    filtro_valor_min = None
    filtro_valor_max = None
    try:
        if filtro_valor_min_raw:
            filtro_valor_min = float(filtro_valor_min_raw.replace(",", "."))
    except ValueError:
        filtro_valor_min_raw = ""
    try:
        if filtro_valor_max_raw:
            filtro_valor_max = float(filtro_valor_max_raw.replace(",", "."))
    except ValueError:
        filtro_valor_max_raw = ""

    resumo["lancamentos"] = buscar_lancamentos(
        db, session["user_id"], ano, mes,
        categoria=filtro_categoria or None,
        descricao=filtro_descricao or None,
        valor_min=filtro_valor_min,
        valor_max=filtro_valor_max,
    )

    filtros_ativos = bool(filtro_categoria or filtro_descricao or filtro_valor_min_raw or filtro_valor_max_raw)

    return render_template(
        "dashboard.html",
        ano=ano,
        mes=mes,
        nome_mes=MESES_PT[mes],
        meses_pt=MESES_PT,
        resumo=resumo,
        user=user,
        categorias_receita=CATEGORIAS_RECEITA,
        categorias_despesa=CATEGORIAS_DESPESA,
        ultima_categoria=session.get("ultima_categoria"),
        filtro_categoria=filtro_categoria,
        filtro_descricao=filtro_descricao,
        filtro_valor_min=filtro_valor_min_raw,
        filtro_valor_max=filtro_valor_max_raw,
        filtros_ativos=filtros_ativos,
    )


def proximo_mes(ano, mes):
    mes += 1
    if mes > 12:
        mes = 1
        ano += 1
    return ano, mes


def sufixo_parcela(descricao_base, atual, total):
    if descricao_base:
        return f"{descricao_base} (parcela {atual}/{total})"
    return f"Parcela {atual}/{total}"


def remover_sufixo_parcela(descricao):
    """Remove o sufixo "(parcela X/Y)" de uma descrição, para exibir só a
    parte digitada pelo usuário ao editar (evita duplicar o sufixo)."""
    if not descricao:
        return descricao
    return re.sub(r"\s*\(parcela \d+/\d+\)\s*$", "", descricao).strip()


def gerar_parcelas(db, user_id, tipo, categoria, descricao_base, valor,
                    ano_ini, mes_ini, grupo, indice_inicial, indice_final):
    """Insere lançamentos para os índices [indice_inicial, indice_final] de uma
    série de parcelas, começando em ano_ini/mes_ini e avançando um mês a cada
    parcela."""
    ano_l, mes_l = ano_ini, mes_ini
    for indice in range(indice_inicial, indice_final + 1):
        db.execute(
            """INSERT INTO lancamentos
               (user_id, tipo, categoria, descricao, valor, ano, mes,
                parcela_atual, parcela_total, parcela_grupo)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id, tipo, categoria, sufixo_parcela(descricao_base, indice, indice_final),
                valor, ano_l, mes_l, indice, indice_final, grupo,
            ),
        )
        ano_l, mes_l = proximo_mes(ano_l, mes_l)


def gerar_fixo(db, user_id, tipo, categoria, descricao, valor, ano_ini, mes_ini, grupo, quantidade):
    """Insere `quantidade` lançamentos de uma despesa fixa, começando em
    ano_ini/mes_ini e avançando um mês a cada um."""
    ano_l, mes_l = ano_ini, mes_ini
    for _ in range(quantidade):
        db.execute(
            """INSERT INTO lancamentos
               (user_id, tipo, categoria, descricao, valor, ano, mes, fixo_grupo, fixo_ativo)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (user_id, tipo, categoria, descricao, valor, ano_l, mes_l, grupo),
        )
        ano_l, mes_l = proximo_mes(ano_l, mes_l)


def estender_fixos_se_necessario(db, user_id, ano_ref, mes_ref):
    """Garante que toda despesa fixa ainda ativa do usuário tenha lançamentos
    gerados com uma boa margem à frente do mês que está sendo visto,
    estendendo automaticamente a série quando necessário. Séries que o
    usuário já encerrou (fixo_ativo = 0) não são mais estendidas."""
    grupos = db.execute(
        """SELECT DISTINCT fixo_grupo FROM lancamentos
           WHERE user_id = ? AND fixo_grupo IS NOT NULL
             AND (fixo_ativo IS NULL OR fixo_ativo = 1)""",
        (user_id,),
    ).fetchall()

    ref_idx = ano_ref * 12 + mes_ref
    algo_mudou = False

    for linha in grupos:
        grupo = linha["fixo_grupo"]
        ultimo = db.execute(
            """SELECT * FROM lancamentos WHERE user_id = ? AND fixo_grupo = ?
               ORDER BY ano DESC, mes DESC LIMIT 1""",
            (user_id, grupo),
        ).fetchone()
        if ultimo is None:
            continue

        ano_atual, mes_atual = ultimo["ano"], ultimo["mes"]
        max_idx = ano_atual * 12 + mes_atual
        tentativas = 0

        # Normalmente uma única extensão já basta, mas usamos um laço para
        # cobrir também o caso de o usuário pular várias dezenas de meses de
        # uma vez (ex.: indo direto para um ano bem à frente).
        while max_idx - ref_idx < MARGEM_FIXO_MESES and tentativas < 50:
            ano_prox, mes_prox = proximo_mes(ano_atual, mes_atual)
            gerar_fixo(
                db, user_id, ultimo["tipo"], ultimo["categoria"], ultimo["descricao"],
                ultimo["valor"], ano_prox, mes_prox, grupo, JANELA_FIXO_EXTENSAO,
            )

            # avança ano_atual/mes_atual para o último mês recém-gerado
            ano_atual, mes_atual = ano_prox, mes_prox
            for _ in range(JANELA_FIXO_EXTENSAO - 1):
                ano_atual, mes_atual = proximo_mes(ano_atual, mes_atual)

            max_idx = ano_atual * 12 + mes_atual
            algo_mudou = True
            tentativas += 1

    if algo_mudou:
        db.commit()


@app.route("/lancamento/adicionar", methods=["POST"])
def adicionar_lancamento():
    db = get_db()
    categoria = request.form["categoria"]
    valor_raw = request.form["valor"].replace(",", ".")
    descricao = request.form.get("descricao", "").strip()
    ano = int(request.form["ano"])
    mes = int(request.form["mes"])
    repeticao = request.form.get("repeticao", "unica")

    try:
        valor = float(valor_raw)
    except ValueError:
        flash("Valor inválido.", "erro")
        return redirect(url_for("dashboard", ano=ano, mes=mes))

    tipo = "receita" if categoria in CATEGORIAS_RECEITA else "despesa"

    if repeticao == "parcelado":
        try:
            num_parcelas = int(request.form.get("num_parcelas", ""))
        except ValueError:
            flash("Informe em quantas parcelas o valor foi dividido.", "erro")
            return redirect(url_for("dashboard", ano=ano, mes=mes))

        if num_parcelas < 2:
            flash("Para lançar como parcelado, informe pelo menos 2 parcelas.", "erro")
            return redirect(url_for("dashboard", ano=ano, mes=mes))

        grupo = str(uuid.uuid4())
        gerar_parcelas(db, session["user_id"], tipo, categoria, descricao, valor,
                        ano, mes, grupo, 1, num_parcelas)
        flash(f"Lançamento parcelado em {num_parcelas}x adicionado.", "sucesso")

    elif repeticao == "fixo":
        grupo = str(uuid.uuid4())
        gerar_fixo(db, session["user_id"], tipo, categoria, descricao, valor,
                   ano, mes, grupo, JANELA_FIXO_INICIAL)
        flash("Despesa fixa adicionada. Ela será lançada automaticamente todo mês.", "sucesso")

    else:
        db.execute(
            """INSERT INTO lancamentos (user_id, tipo, categoria, descricao, valor, ano, mes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session["user_id"], tipo, categoria, descricao, valor, ano, mes),
        )

    db.commit()
    session["ultima_categoria"] = categoria
    return redirect(url_for("dashboard", ano=ano, mes=mes))


@app.route("/lancamento/salario-rapido", methods=["POST"])
def salario_rapido():
    db = get_db()
    ano = int(request.form["ano"])
    mes = int(request.form["mes"])
    user = db.execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()

    if user["salario_padrao"] and user["salario_padrao"] > 0:
        db.execute(
            """INSERT INTO lancamentos (user_id, tipo, categoria, descricao, valor, ano, mes)
               VALUES (?, 'receita', 'Salário', 'Salário do mês', ?, ?, ?)""",
            (session["user_id"], user["salario_padrao"], ano, mes),
        )
        db.commit()
        flash("Salário lançado.", "sucesso")
    else:
        flash("Você não tem um salário padrão configurado.", "erro")

    return redirect(url_for("dashboard", ano=ano, mes=mes))


@app.route("/lancamento/editar/<int:lancamento_id>", methods=["GET", "POST"])
def editar_lancamento(lancamento_id):
    db = get_db()
    user_id = session["user_id"]
    lancamento = db.execute(
        "SELECT * FROM lancamentos WHERE id = ? AND user_id = ?",
        (lancamento_id, user_id),
    ).fetchone()

    if lancamento is None:
        flash("Lançamento não encontrado.", "erro")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        categoria = request.form["categoria"]
        valor_raw = request.form["valor"].replace(",", ".")
        descricao = request.form.get("descricao", "").strip()
        ano = lancamento["ano"]
        mes = lancamento["mes"]
        repeticao_nova = request.form.get("repeticao", "unica")

        try:
            valor = float(valor_raw)
        except ValueError:
            flash("Valor inválido.", "erro")
            return redirect(url_for("editar_lancamento", lancamento_id=lancamento_id))

        tipo = "receita" if categoria in CATEGORIAS_RECEITA else "despesa"

        estado_antigo = (
            "parcelado" if lancamento["parcela_grupo"]
            else "fixo" if lancamento["fixo_grupo"]
            else "unica"
        )

        # Se o lançamento fazia parte de uma série (parcelado ou fixo) e o
        # usuário está mudando para um estado diferente, encerramos a série
        # antiga a partir deste mês (apaga só as ocorrências futuras, mantém
        # o histórico) e desvinculamos esta linha da série antiga.
        if estado_antigo in ("parcelado", "fixo") and repeticao_nova != estado_antigo:
            campo_grupo = "parcela_grupo" if estado_antigo == "parcelado" else "fixo_grupo"
            grupo_antigo = lancamento[campo_grupo]
            db.execute(
                f"""DELETE FROM lancamentos WHERE user_id = ? AND {campo_grupo} = ?
                    AND (ano > ? OR (ano = ? AND mes > ?))""",
                (user_id, grupo_antigo, ano, ano, mes),
            )
            if estado_antigo == "fixo":
                # Marca a série como encerrada para que a extensão automática
                # não volte a gerar meses futuros para ela.
                db.execute(
                    "UPDATE lancamentos SET fixo_ativo = 0 WHERE fixo_grupo = ?",
                    (grupo_antigo,),
                )
            db.execute(
                """UPDATE lancamentos
                   SET parcela_atual = NULL, parcela_total = NULL, parcela_grupo = NULL, fixo_grupo = NULL
                   WHERE id = ?""",
                (lancamento_id,),
            )

        if repeticao_nova == "parcelado":
            try:
                num_parcelas = int(request.form.get("num_parcelas", ""))
            except ValueError:
                flash("Informe em quantas parcelas o valor foi dividido.", "erro")
                return redirect(url_for("editar_lancamento", lancamento_id=lancamento_id))

            if num_parcelas < 2:
                flash("Para marcar como parcelado, informe pelo menos 2 parcelas.", "erro")
                return redirect(url_for("editar_lancamento", lancamento_id=lancamento_id))

            if estado_antigo == "parcelado" and lancamento["parcela_grupo"]:
                # Continua na mesma série: só ajusta a quantidade de parcelas
                # se ela mudou, e atualiza esta ocorrência normalmente.
                grupo = lancamento["parcela_grupo"]
                atual = lancamento["parcela_atual"]
                total_antigo = lancamento["parcela_total"]

                if num_parcelas != total_antigo:
                    # Atualiza também o sufixo "(parcela X/Y)" gravado na
                    # descrição de cada linha da série, já que o total mudou.
                    linhas_grupo = db.execute(
                        "SELECT id, descricao, parcela_atual FROM lancamentos WHERE parcela_grupo = ?",
                        (grupo,),
                    ).fetchall()
                    for linha_g in linhas_grupo:
                        base = remover_sufixo_parcela(linha_g["descricao"])
                        nova_descricao = sufixo_parcela(base, linha_g["parcela_atual"], num_parcelas)
                        db.execute(
                            "UPDATE lancamentos SET descricao = ?, parcela_total = ? WHERE id = ?",
                            (nova_descricao, num_parcelas, linha_g["id"]),
                        )
                    db.execute(
                        "DELETE FROM lancamentos WHERE parcela_grupo = ? AND parcela_atual > ?",
                        (grupo, num_parcelas),
                    )
                    maior = db.execute(
                        "SELECT COALESCE(MAX(parcela_atual), 0) AS m FROM lancamentos WHERE parcela_grupo = ?",
                        (grupo,),
                    ).fetchone()["m"]
                    if maior < num_parcelas:
                        ultimo = db.execute(
                            """SELECT * FROM lancamentos WHERE parcela_grupo = ?
                               ORDER BY ano DESC, mes DESC LIMIT 1""",
                            (grupo,),
                        ).fetchone()
                        ano_prox, mes_prox = proximo_mes(ultimo["ano"], ultimo["mes"])
                        gerar_parcelas(db, user_id, tipo, categoria, descricao, valor,
                                        ano_prox, mes_prox, grupo, maior + 1, num_parcelas)

                db.execute(
                    """UPDATE lancamentos SET tipo = ?, categoria = ?, descricao = ?, valor = ?
                       WHERE id = ?""",
                    (tipo, categoria, sufixo_parcela(descricao, atual, num_parcelas), valor, lancamento_id),
                )
            else:
                # Era um lançamento único (ou acabou de sair de outra série):
                # esta ocorrência vira a parcela 1 de uma série nova.
                grupo = str(uuid.uuid4())
                db.execute(
                    """UPDATE lancamentos
                       SET tipo = ?, categoria = ?, descricao = ?, valor = ?,
                           parcela_atual = 1, parcela_total = ?, parcela_grupo = ?
                       WHERE id = ?""",
                    (tipo, categoria, sufixo_parcela(descricao, 1, num_parcelas), valor,
                     num_parcelas, grupo, lancamento_id),
                )
                if num_parcelas > 1:
                    ano_prox, mes_prox = proximo_mes(ano, mes)
                    gerar_parcelas(db, user_id, tipo, categoria, descricao, valor,
                                    ano_prox, mes_prox, grupo, 2, num_parcelas)

            flash("Lançamento parcelado atualizado.", "sucesso")

        elif repeticao_nova == "fixo":
            if estado_antigo == "fixo" and lancamento["fixo_grupo"]:
                # Continua fixo: só atualiza esta ocorrência.
                db.execute(
                    """UPDATE lancamentos SET tipo = ?, categoria = ?, descricao = ?, valor = ?
                       WHERE id = ?""",
                    (tipo, categoria, descricao, valor, lancamento_id),
                )
            else:
                # Vira o início de uma nova despesa fixa a partir deste mês.
                grupo = str(uuid.uuid4())
                db.execute(
                    """UPDATE lancamentos
                       SET tipo = ?, categoria = ?, descricao = ?, valor = ?,
                           fixo_grupo = ?, fixo_ativo = 1
                       WHERE id = ?""",
                    (tipo, categoria, descricao, valor, grupo, lancamento_id),
                )
                ano_prox, mes_prox = proximo_mes(ano, mes)
                gerar_fixo(db, user_id, tipo, categoria, descricao, valor,
                           ano_prox, mes_prox, grupo, JANELA_FIXO_INICIAL - 1)

            flash("Despesa fixa atualizada.", "sucesso")

        else:
            db.execute(
                """UPDATE lancamentos
                   SET tipo = ?, categoria = ?, descricao = ?, valor = ?
                   WHERE id = ? AND user_id = ?""",
                (tipo, categoria, descricao, valor, lancamento_id, user_id),
            )
            flash("Lançamento atualizado.", "sucesso")

        db.commit()
        return redirect(url_for("dashboard", ano=ano, mes=mes))

    repeticao_atual = (
        "parcelado" if lancamento["parcela_grupo"]
        else "fixo" if lancamento["fixo_grupo"]
        else "unica"
    )

    return render_template(
        "editar_lancamento.html",
        lancamento=lancamento,
        categorias_receita=CATEGORIAS_RECEITA,
        categorias_despesa=CATEGORIAS_DESPESA,
        repeticao_atual=repeticao_atual,
        descricao_exibicao=remover_sufixo_parcela(lancamento["descricao"]),
    )


@app.route("/lancamento/excluir/<int:lancamento_id>", methods=["POST"])
def excluir_lancamento(lancamento_id):
    db = get_db()
    ano = request.form.get("ano")
    mes = request.form.get("mes")
    db.execute(
        "DELETE FROM lancamentos WHERE id = ? AND user_id = ?",
        (lancamento_id, session["user_id"]),
    )
    db.commit()
    return redirect(url_for("dashboard", ano=ano, mes=mes))


@app.route("/resumo")
def resumo():
    db = get_db()
    hoje = datetime.now()
    # Mostra meses passados + o mês atual + o próximo mês (um mês de margem
    # à frente, útil pra já ver o que está programado sem exibir tudo que
    # foi gerado lá na frente pelas parcelas/despesas fixas).
    ano_limite, mes_limite = proximo_mes(hoje.year, hoje.month)
    meses, total_guardado_geral = resumo_todos_os_meses(db, session["user_id"], ano_limite, mes_limite)
    return render_template(
        "resumo.html", meses=meses, total_guardado_geral=total_guardado_geral
    )


@app.route("/parcelas")
def parcelas_abertas():
    db = get_db()
    hoje = datetime.now()
    ano = int(request.args.get("ano", hoje.year))
    mes = int(request.args.get("mes", hoje.month))

    resumo = resumo_parcelas_abertas(db, session["user_id"], ano, mes)

    return render_template(
        "parcelas.html",
        ano=ano,
        mes=mes,
        nome_mes=MESES_PT[mes],
        meses_pt=MESES_PT,
        resumo=resumo,
    )


@app.route("/configuracoes", methods=["GET", "POST"])
def configuracoes():
    db = get_db()
    if request.method == "POST":
        salario_padrao = request.form.get("salario_padrao", "0").replace(",", ".") or "0"
        try:
            valor = float(salario_padrao)
        except ValueError:
            valor = 0
        db.execute(
            "UPDATE users SET salario_padrao = ? WHERE id = ?",
            (valor, session["user_id"]),
        )
        db.commit()
        flash("Configurações salvas.", "sucesso")
        return redirect(url_for("configuracoes"))

    user = db.execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()
    return render_template("configuracoes.html", user=user)


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
else:
    init_db()
