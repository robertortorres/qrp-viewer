# QRP Viewer

Sistema web para organizar exames de **campimetria visual** exportados pelo perímetro **Eyetec** no formato `.QRP`.

Você envia o arquivo QRP e o sistema faz o resto:

- lê os dados do exame (paciente, data de nascimento, idade, data e hora, responsável, olho, MD, PSD e índices de confiabilidade);
- cria o cadastro do paciente, ou anexa o exame ao cadastro que já existe;
- converte o laudo em PDF A4, que fica disponível para visualizar, baixar e imprimir.

Ele roda em Docker com PostgreSQL e foi pensado para ficar atrás do Nginx Proxy Manager.

## Funcionalidades

**Importação**
- Aceita vários arquivos de uma vez, arrastando para a tela.
- O paciente é identificado pelo nome normalizado (sem acentos e sem diferença de maiúsculas) mais a data de nascimento.
- Arquivos repetidos são detectados pelo hash SHA-256 e recusados.

**Paciente**
- Os exames aparecem agrupados por dia, com OD (vermelho) e OE (azul).
- Cada exame mostra MD, PSD e confiabilidade.
- Há um alerta quando a perda de fixação passa de 20% ou FP/FN passam de 33%.

**Exame**
- O PDF fica embutido na página, com botões para imprimir, baixar e abrir em nova aba.
- Há um atalho para o outro olho do mesmo dia.
- O QRP original fica guardado, e o PDF pode ser gerado novamente.

**Cadastro**
- O nome, a data de nascimento e as observações podem ser editados.
- Um administrador pode unir cadastros duplicados, por exemplo quando a grafia do nome muda.

**Usuários**
- Há dois perfis, administrador e usuário.
- A troca de senha é obrigatória no primeiro acesso, e a conta é bloqueada após tentativas erradas.
- O sistema não deixa ficar sem nenhum administrador ativo.

**Auditoria**
- Registra entradas no sistema, importações, visualizações, downloads, edições e exclusões, com usuário e IP.

**Segurança**
- Senhas com hash Argon2, sessão em cookie assinado (HttpOnly, SameSite e Secure) e proteção CSRF.
- Cabeçalhos de segurança: CSP, X-Frame-Options e nosniff.

| Ação | Usuário | Administrador |
|---|:-:|:-:|
| Importar, visualizar, baixar e imprimir exames | ✓ | ✓ |
| Editar cadastro de paciente | ✓ | ✓ |
| Unir cadastros, excluir exames e pacientes | | ✓ |
| Gerenciar usuários e ver a auditoria | | ✓ |

## Como o QRP é lido

O `.QRP` da Eyetec é um cabeçalho proprietário curto seguido de um **EMF** (Enhanced Metafile do Windows) com o laudo impresso. O sistema faz o seguinte:

1. localiza o EMF dentro do arquivo;
2. lê os registros de texto `EMR_EXTTEXTOUTW`, que trazem as coordenadas de cada texto, e pareia cada rótulo (`Paciente:`, `Data e Hora:`, `Olho:`...) com o valor à direita dele na mesma linha;
3. converte o EMF em PDF com o **LibreOffice Draw headless**;
4. reescala a página para preencher o A4, porque o EMF vem com referência de tela.

O laudo usa a fonte Tahoma, que não existe no Linux. A imagem substitui a Tahoma pela Inter via fontconfig (`docker/fonts.conf`): nos testes, foi a substituta mais fiel ao laudo original, incluindo o negrito dos rótulos. O texto do PDF continua vetorial e pesquisável.

Para testar um arquivo fora do sistema:

```bash
docker compose run --rm app python -m app.qrp /data/exame.qrp /data/exame.pdf
```

## Instalação

Requisitos: Docker com o plugin Compose.

```bash
git clone https://github.com/robertortorres/qrp-viewer.git
cd qrp-viewer
cp .env.example .env
```

Edite o `.env`. Gere os segredos com `openssl rand -base64 48` e defina pelo menos `POSTGRES_PASSWORD`, `SECRET_KEY` e `ADMIN_PASSWORD`. Depois suba os containers:

```bash
docker compose up -d --build
docker compose logs -f app      # aguarde "Application startup complete"
```

Entre com `ADMIN_USERNAME` e `ADMIN_PASSWORD`. O sistema vai pedir uma senha nova no primeiro acesso. O admin só é criado quando o banco está vazio: depois disso, mudar `ADMIN_PASSWORD` no `.env` não tem efeito.

Também dá para usar a imagem pronta do GitHub Container Registry, gerada pelo CI a cada push na `main`, em vez de compilar localmente:

```bash
docker compose pull && docker compose up -d
```

### Atrás do Nginx Proxy Manager

Por padrão, o app escuta em `127.0.0.1:8000` no host. No NPM, crie um Proxy Host com esta configuração:

- **Domain:** `campimetria.suaclinica.com.br`
- **Scheme / Forward Hostname / Port:** `http` / IP do host Docker (ou `qrp-viewer`, se o NPM estiver na mesma rede Docker) / `8000`
- **Block Common Exploits:** ligado
- **SSL:** certificado Let's Encrypt, com *Force SSL* e *HTTP/2* ligados

Se o upload falhar com erro **413**, acrescente em *Advanced → Custom Nginx Configuration*:

```nginx
client_max_body_size 100m;
```

Se o NPM e o app estiverem em hosts diferentes, troque no `.env` para `APP_BIND=0.0.0.0` e libere a porta só para o IP do NPM no firewall.

Se estiverem no mesmo host, prefira a rede Docker do NPM. No `docker-compose.yml`:

1. descomente a rede `proxy` (ajuste o `name` para a rede do seu NPM);
2. adicione `proxy` em `app.networks`;
3. remova o bloco `ports`.

O uvicorn roda com `--proxy-headers`, então a auditoria registra o IP real do cliente e o cookie de sessão sai como `Secure`. Deixe `COOKIE_SECURE=true` sempre que o acesso for por HTTPS. Use `false` só para testes em HTTP puro, senão o login não funciona.

## Configuração (`.env`)

| Variável | Padrão | Descrição |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `qrp` / — / `qrp` | Credenciais do banco |
| `SECRET_KEY` | — | Assina os cookies de sessão. **Obrigatória.** Trocar desconecta todos |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / — | Admin criado no primeiro start |
| `COOKIE_SECURE` | `true` | Cookie só via HTTPS |
| `APP_BIND` / `APP_PORT` | `127.0.0.1` / `8000` | Onde publicar a porta no host |
| `APP_NAME` | `QRP Viewer` | Nome exibido na interface |
| `SESSION_MAX_AGE` | `28800` | Sessão expira após este tempo sem uso (em segundos) |
| `MAX_UPLOAD_MB` | `50` | Tamanho máximo por arquivo |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCK_MINUTES` | `5` / `15` | Bloqueio por tentativas erradas |
| `CONVERT_CONCURRENCY` | `2` | Conversões simultâneas do LibreOffice |

## Dados e backup

| O quê | Onde |
|---|---|
| Cadastros, metadados dos exames, usuários e auditoria | volume `qrp_pg` (PostgreSQL) |
| Arquivos QRP originais e PDFs gerados | volume `qrp_data`, em `/data/exams/NNNN/<id>.qrp` e `<id>.pdf` |

Faça backup **dos dois**. Exemplo:

```bash
docker compose exec -T db pg_dump -U qrp -Fc qrp > backup/qrp_$(date +%F).dump
docker run --rm -v qrp-viewer_qrp_data:/data -v "$PWD/backup":/b alpine \
  tar czf /b/qrp_files_$(date +%F).tgz -C /data .
```

Os PDFs podem ser regerados a partir dos QRPs. Os QRPs originais são a fonte primária.

## Atualização

```bash
git pull
docker compose up -d --build   # ou: docker compose pull && docker compose up -d
```

As tabelas são criadas automaticamente na inicialização. Mudanças futuras de schema devem vir com migrações (Alembic).

## LGPD

Os exames são dados pessoais sensíveis de saúde.

- **Nunca versione arquivos `.QRP` ou PDFs de pacientes.** O `.gitignore` já bloqueia esses formatos.
- Use HTTPS, contas individuais (nada de usuário compartilhado) e revise a auditoria periodicamente.
- Excluir um paciente ou exame apaga os arquivos do disco. Os backups seguem a sua política de retenção.

## Desenvolvimento

```bash
pip install -r requirements.txt pytest httpx
# requer LibreOffice instalado localmente para a conversão
TEST_DATABASE_URL=postgresql+psycopg://qrp:qrp@localhost:5432/qrp_test \
QRP_SAMPLE=/caminho/fora/do/repo/exame.qrp \
pytest -v
```

Sem `QRP_SAMPLE`, o teste de importação é pulado. É assim que roda no CI, porque o repositório não guarda exames reais.

Estrutura do código:

```
app/
  qrp.py          leitura do QRP e conversão para PDF
  main.py         app FastAPI, middlewares e inicialização
  models.py       User, Patient, Exam, AuditLog
  security.py     senhas, sessão, CSRF, permissões e auditoria
  routers/        auth, patients (pacientes/exames/importação), users
  templates/      Jinja2
  static/         CSS, JS, fontes e ícones
```

## Ideias para próximas versões

- Visão OD e OE lado a lado do mesmo dia, numa página só, para impressão.
- Gráfico da evolução do MD ao longo do tempo por olho (progressão).
- Importação automática de uma pasta compartilhada (SMB) onde o perímetro grava os QRPs.
- CPF e telefone no cadastro, e busca por eles.
- Login integrado (OIDC) e segundo fator para administradores.
- Migrações com Alembic.

## Créditos

A fonte de interface é a [Atkinson Hyperlegible](https://www.brailleinstitute.org/freefont/), do Braille Institute, criada para leitores com baixa visão e licenciada sob a SIL Open Font License.

O software é distribuído sob a licença MIT.
