# Portal de Investidores — Flask + Supabase

## Estrutura
```
portal/
├── app.py                  # Entry point Flask
├── config.py               # Configurações (env vars)
├── requirements.txt        # Dependências Python
├── vercel.json             # Deploy Vercel
├── .env.example            # Variáveis de ambiente necessárias
│
├── auth/
│   ├── __init__.py
│   └── routes.py           # login, logout, reset, activate
│
├── admin/
│   ├── __init__.py
│   └── routes.py           # dashboard, investidores, empresas, docs
│
├── portal/
│   ├── __init__.py
│   └── routes.py           # home, consolidado, empresa, extrato, docs
│
├── services/
│   ├── supabase_client.py  # cliente Supabase singleton
│   ├── auth_service.py     # login, sessão, preview
│   ├── investidor_service.py
│   ├── empresa_service.py
│   ├── financeiro_service.py
│   ├── documento_service.py
│   └── benchmark_service.py  # API BACEN + cache
│
├── middleware/
│   └── auth_guard.py       # decorators: @requer_login, @requer_admin
│
└── templates/
    ├── base.html            # layout base com sidebar
    ├── auth/
    │   ├── login.html
    │   ├── reset.html
    │   └── activate.html
    ├── admin/
    │   ├── layout.html
    │   ├── dashboard.html
    │   ├── investidores.html
    │   ├── investidor_form.html
    │   ├── empresas.html
    │   └── documentos.html
    └── portal/
        ├── layout.html
        ├── home.html
        ├── consolidado.html
        ├── empresa.html
        ├── extrato.html
        └── documentos.html
```
