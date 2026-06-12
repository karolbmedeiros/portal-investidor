# Portal de Investidores

Flask + Supabase. Dois acessos separados: **admin** (gestora) e **investidor** (leitura).

---

## Pré-requisitos

- Python 3.11+
- Conta no [Supabase](https://supabase.com)

---

## 1. Configurar o Supabase

### 1.1 Criar o banco
No SQL Editor do Supabase, execute o arquivo inteiro:
```
supabase/schema.sql
```

### 1.2 Criar o bucket de documentos
Supabase Dashboard → Storage → New bucket:
- Nome: `documentos`
- Acesso: **privado** (desmarque "Public bucket")

### 1.3 Criar o admin
No Supabase Dashboard → Authentication → Users → Invite user:
- E-mail do admin
- Depois edite o usuário e adicione em `user_metadata`:
```json
{ "role": "admin", "nome": "Seu Nome" }
```

### 1.4 Pegar as chaves
Settings → API:
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY` (anon/public)
- `SUPABASE_SERVICE_KEY` (service_role — mantenha seguro)

---

## 2. Rodar localmente

```bash
# Clone / entre na pasta
cd portal

# Crie o ambiente virtual
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# Instale dependências
pip install -r requirements.txt

# Configure o .env
cp .env.example .env
# Edite .env com suas chaves do Supabase

# Rode
python app.py
```

Acesse: http://localhost:5000

---

## 3. Deploy no Vercel

```bash
# Instale a CLI do Vercel (se não tiver)
npm i -g vercel

# Na pasta do projeto
vercel

# Siga o wizard. Quando perguntar o framework: Other

# Configure as variáveis de ambiente no Vercel Dashboard:
# Settings → Environment Variables:
# SUPABASE_URL
# SUPABASE_ANON_KEY
# SUPABASE_SERVICE_KEY
# FLASK_SECRET_KEY  (gere com: python -c "import secrets; print(secrets.token_hex(32))")
```

> O `vercel.json` já está configurado para servir o Flask como serverless function.

---

## Estrutura de acessos

| Perfil | Login | Acesso |
|--------|-------|--------|
| Admin | E-mail + senha (role: admin) | `/admin/*` — gestão completa |
| Investidor | E-mail + senha (role: investidor) | `/portal/*` — só seus dados |

### Fluxo de cadastro de investidor

1. Admin acessa `/admin/investidores/novo`
2. Preenche nome, CPF, e-mail e vincula empresas
3. Sistema cria conta no Supabase Auth e envia e-mail de ativação
4. Investidor clica no link → define senha → acessa o portal

### Modo preview (admin)

Admin pode ver o portal exatamente como o investidor vê:
- Em `/admin/investidores` → botão "Ver portal"
- Banner âmbar aparece no topo indicando o modo
- "Sair do preview" volta ao admin

---

## Rotas principais

### Admin (`/admin/...`)
| Rota | Descrição |
|------|-----------|
| `/admin/` | Dashboard com contadores |
| `/admin/investidores` | Lista, cria, edita investidores |
| `/admin/empresas` | Cadastra empresas e categorias de receita/custo |
| `/admin/importar` | Upload de CSV com movimentações |
| `/admin/documentos` | Upload e gerenciamento de arquivos |

### Portal do investidor (`/portal/...`)
| Rota | Descrição |
|------|-----------|
| `/portal/home` | Cards de resumo + lista de empresas |
| `/portal/consolidado` | Gráficos + benchmarks |
| `/portal/empresa/<id>` | KPIs + resultado expansível |
| `/portal/extrato` | Transações filtráveis |
| `/portal/documentos` | Download de arquivos |

### Auth (`/auth/...`)
| Rota | Descrição |
|------|-----------|
| `/auth/login` | Login |
| `/auth/logout` | Logout |
| `/auth/reset` | Solicitar reset de senha |
| `/auth/ativar?token=...` | Ativar conta e definir senha |

---

## CSV de importação

Formato esperado:
```csv
data,tipo,categoria_id,descricao,valor
2024-01-15,receita,<uuid-categoria>,Aluguel semana 1,3200.00
2024-01-15,custo,<uuid-categoria>,Seguro janeiro,-450.00
```

- `categoria_id` é opcional
- `tipo`: `receita`, `custo` ou `resultado`
- `valor` negativo para custos (ou positivo — o sistema trata pelo `tipo`)
