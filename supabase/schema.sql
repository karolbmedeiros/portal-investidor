-- ============================================================
-- Portal de Investidores — Schema Supabase (PostgreSQL)
-- Execute no SQL Editor do Supabase
-- ============================================================

-- ── Extensões ────────────────────────────────────────────────
create extension if not exists "uuid-ossp";

-- ── Tabelas ──────────────────────────────────────────────────

create table if not exists investidores (
  id          uuid primary key references auth.users(id) on delete cascade,
  nome        text not null,
  cpf         text,
  email       text not null,
  ativo       boolean default true,
  created_at  timestamptz default now()
);

create table if not exists empresas (
  id          uuid primary key default uuid_generate_v4(),
  nome        text not null,
  tipo        text,
  descricao   text,
  created_at  timestamptz default now()
);

create table if not exists investidor_empresas (
  investidor_id  uuid references investidores(id) on delete cascade,
  empresa_id     uuid references empresas(id) on delete cascade,
  data_inicio    date default current_date,
  primary key (investidor_id, empresa_id)
);

create table if not exists categorias (
  id          uuid primary key default uuid_generate_v4(),
  empresa_id  uuid references empresas(id) on delete cascade,
  nome        text not null,
  tipo        text check (tipo in ('receita', 'custo')) not null,
  created_at  timestamptz default now()
);

create table if not exists transacoes (
  id             uuid primary key default uuid_generate_v4(),
  investidor_id  uuid references investidores(id) on delete cascade,
  empresa_id     uuid references empresas(id) on delete cascade,
  categoria_id   uuid references categorias(id) on delete set null,
  data           date not null,
  tipo           text check (tipo in ('receita', 'custo', 'resultado')) not null,
  descricao      text not null,
  valor          numeric(14, 2) not null,
  created_at     timestamptz default now()
);

create table if not exists saldos (
  id             uuid primary key default uuid_generate_v4(),
  investidor_id  uuid references investidores(id) on delete cascade,
  empresa_id     uuid references empresas(id) on delete cascade,
  data           date not null,
  saldo          numeric(14, 2) not null,
  created_at     timestamptz default now(),
  unique (investidor_id, empresa_id, data)
);

create table if not exists documentos (
  id               uuid primary key default uuid_generate_v4(),
  empresa_id       uuid references empresas(id) on delete cascade,
  investidor_id    uuid references investidores(id) on delete set null,
  nome             text not null,
  categoria        text not null,
  url              text not null,
  caminho_storage  text not null,
  mime_type        text,
  visibilidade     text default 'todos' check (visibilidade in ('todos', 'especifico')),
  created_at       timestamptz default now()
);

create table if not exists benchmark_cache (
  id          uuid primary key default uuid_generate_v4(),
  indice      text not null,
  dados       jsonb not null,
  data_cache  date not null,
  created_at  timestamptz default now()
);

create table if not exists audit_log (
  id          uuid primary key default uuid_generate_v4(),
  usuario_id  uuid,
  acao        text not null,
  tabela      text,
  registro_id text,
  detalhes    jsonb,
  created_at  timestamptz default now()
);

-- ── Índices ───────────────────────────────────────────────────
create index if not exists idx_transacoes_investidor on transacoes(investidor_id);
create index if not exists idx_transacoes_empresa    on transacoes(empresa_id);
create index if not exists idx_transacoes_data       on transacoes(data desc);
create index if not exists idx_saldos_investidor     on saldos(investidor_id);
create index if not exists idx_saldos_data           on saldos(data desc);
create index if not exists idx_documentos_empresa    on documentos(empresa_id);

-- ── Row Level Security ────────────────────────────────────────
-- O backend Flask usa service_role (bypassa RLS).
-- Habilitar RLS protege acesso direto via anon/JWT.

alter table investidores      enable row level security;
alter table empresas          enable row level security;
alter table investidor_empresas enable row level security;
alter table categorias        enable row level security;
alter table transacoes        enable row level security;
alter table saldos            enable row level security;
alter table documentos        enable row level security;

-- Admins veem tudo (role admin no user_metadata)
create policy "admin_acesso_total" on investidores
  for all using (auth.jwt() ->> 'role' = 'admin');

create policy "admin_acesso_total" on empresas
  for all using (auth.jwt() ->> 'role' = 'admin');

create policy "admin_acesso_total" on transacoes
  for all using (auth.jwt() ->> 'role' = 'admin');

create policy "admin_acesso_total" on saldos
  for all using (auth.jwt() ->> 'role' = 'admin');

create policy "admin_acesso_total" on documentos
  for all using (auth.jwt() ->> 'role' = 'admin');

-- Investidor vê apenas seus próprios dados
create policy "investidor_proprios_dados" on transacoes
  for select using (auth.uid() = investidor_id);

create policy "investidor_proprios_dados" on saldos
  for select using (auth.uid() = investidor_id);

create policy "investidor_proprias_empresas" on investidor_empresas
  for select using (auth.uid() = investidor_id);

create policy "investidor_docs_visiveis" on documentos
  for select using (
    visibilidade = 'todos'
    or investidor_id = auth.uid()
  );

-- ── Storage bucket ────────────────────────────────────────────
-- Criar manualmente no Supabase Dashboard:
-- Storage → New bucket → nome: "documentos" → privado (sem acesso público)
-- Ou via SQL:
-- insert into storage.buckets (id, name, public) values ('documentos', 'documentos', false);

-- ── Relatório do Investidor ───────────────────────────────────
-- Relatório mensal de panorama (.pdf/.docx) publicado pelo admin.
-- O investidor apenas baixa. Mês sem linha (ou com status
-- 'indisponivel') é considerado indisponível — nenhum registro
-- vazio é criado.
--
-- ativo_id é polimórfico: uuid da usina, ou slug da empresa de
-- carros (que não tem tabela, é derivada de planilha).
create table if not exists relatorios_investidor (
  id              uuid primary key default gen_random_uuid(),
  ativo_tipo      text not null check (ativo_tipo in ('usina','carros')),
  ativo_id        text not null,
  mes_referencia  text not null,          -- 'YYYY-MM'
  storage_key     text not null,
  nome_arquivo    text not null,
  mime_type       text,
  file_size       int,
  status          text not null default 'disponivel'
                  check (status in ('disponivel','indisponivel')),
  data_publicacao timestamptz default now(),
  data_upload     timestamptz default now(),
  uploaded_by     uuid,
  created_at      timestamptz default now(),
  -- permite republicar o mesmo mês por upsert
  unique (ativo_tipo, ativo_id, mes_referencia)
);

create index if not exists idx_relatorios_investidor_ativo
  on relatorios_investidor (ativo_tipo, ativo_id, mes_referencia desc);

-- Bucket privado (criado via API do storage neste projeto):
-- insert into storage.buckets (id, name, public)
--   values ('relatorios-investidor', 'relatorios-investidor', false);

-- ── Acessos do investidor ─────────────────────────────────────
-- Permissões de seção e usinas visíveis, que antes moravam no
-- user_metadata do Auth. Saíram de lá porque a home do portal
-- precisa reler o acesso a cada carregamento (para uma revogação
-- valer na hora) e a API admin do Auth custa ~0,7 s por chamada,
-- contra ~0,2 s de uma consulta comum a esta tabela.
--
-- usina_ids é text[] e não uuid[]: guarda uuid de usina e também
-- slug de empresa de carros, que não tem tabela própria.
create table if not exists usuario_acessos (
  user_id     uuid primary key,
  permissions text[] not null default '{}',
  usina_ids   text[] not null default '{}',
  updated_at  timestamptz not null default now()
);

-- Só o service_role toca nesta tabela; o investidor nunca a lê
-- direto, sempre através da sessão do servidor.
alter table usuario_acessos enable row level security;
