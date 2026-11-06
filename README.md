# Bot de promoções (Discord + Telegram, via GitHub Actions)

Avisa no Discord e/ou Telegram só das promoções que casam com os seus interesses (Promobit e, quando não bloqueado, Pelando).

## Comandos do bot
| Comando | Exemplo |
|---|---|
| `/adicionar` (ou `!adicionar` no Discord) | `/adicionar creatina` · `/adicionar ssd 1tb ate 400` · `/adicionar galaxy s24 -capa -pelicula` |
| `/remover` | `/remover creatina` |
| `/lista` | mostra seus interesses |

Todas as palavras do termo precisam estar no título. `ate N` define preço máximo; `-palavra` exclui.

## Configuração

O bot usa **Discord e/ou Telegram**. Cada canal só liga se os secrets dele existirem, então dá para começar só com o Discord e adicionar o Telegram depois (sem mudar código).

### Discord
1. https://discord.com/developers/applications → **New Application** → aba **Bot** → **Reset Token** e copie o token.
2. Ainda na aba **Bot**, ligue **MESSAGE CONTENT INTENT** (sem isso o bot não enxerga o texto dos comandos).
3. Aba **OAuth2 → URL Generator**: marque o escopo `bot` e as permissões *View Channels*, *Send Messages*, *Read Message History*. Abra a URL gerada e adicione o bot ao seu servidor (crie um servidor só seu, privado).
4. No Discord: *Configurações → Avançado → Modo desenvolvedor*. Clique com o botão direito no canal de alertas → **Copiar ID do canal**.
5. No GitHub: *Settings → Secrets and variables → Actions* e crie:
   - `DISCORD_TOKEN` = token do passo 1
   - `DISCORD_CHANNEL_ID` = ID do passo 4

### Telegram (quando liberar a criação de bots)
1. `@BotFather` → `/newbot` → copie o token.
2. Crie o secret `TELEGRAM_TOKEN` com ele. Pronto: o mesmo código passa a rodar nos dois canais.

### Estado criptografado (permite repo público)
Interesses e IDs ficam em `state.enc`, cifrado com AES-256 usando o secret `STATE_KEY` (qualquer senha longa e aleatória, ex.: gerada com `openssl rand -base64 32`). O texto legível nunca vai para o repositório. **Guarde a `STATE_KEY`**: se perdê-la ou trocá-la, o estado não abre mais (basta apagar `state.enc` e recomeçar).

### Comuns
- *Settings → Actions → General → Workflow permissions* → **Read and write** (o bot salva `state.json`).
- *Actions → Promo bot → Run workflow* para testar na hora (depois roda sozinho no agendamento).
- O primeiro usuário a mandar comando em cada canal vira o **dono** desse canal; os demais são ignorados. No Discord, mande `!start` ou `/start` no canal (o Discord pode tentar tratar `/` como comando próprio; se reclamar, use `!`).
- Os alertas são enviados para todos os canais ativos ao mesmo tempo, e os interesses são compartilhados entre eles.

## Limitações
- Comandos e alertas são processados a cada rodada do agendador (o cron do GitHub atrasa às vezes). A 1ª rodada só memoriza as ofertas existentes, sem enviar nada.
- Cada rodada percorre ~10 páginas do Promobit (~120 ofertas recentes; ajustável com a variável `PROMOBIT_PAGES`). Se o intervalo entre rodadas for de horas, ofertas mais antigas que isso podem passar.
- O agendamento (`cron`) do GitHub Actions é pouco confiável: pode rodar com horas de intervalo. Para ter rodadas de 5 em 5 minutos de verdade, dispare o workflow por um serviço externo (cron-job.org → API `workflow_dispatch`).
- Pelando usa Cloudflare e normalmente devolve 403; nesse caso é ignorado (aparece no log).
- O estado vai cifrado para o repositório (`state.enc`). Os logs do Actions de um repo público são visíveis a todos: eles mostram só contagens (ex.: "interesses: 2"), nunca os termos.
- Minutos grátis: repo privado = 2000 min/mês → use `*/30`. Repo público é ilimitado → use `*/5` (o mínimo do GitHub; na prática o agendador pode atrasar).

## Testes
`python3 -c "import sys;sys.path[:0]=['tests','.'];import test_bot as t;[getattr(t,n)() for n in dir(t) if n.startswith('test_')]"`
