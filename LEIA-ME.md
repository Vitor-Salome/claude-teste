# Relatório semanal Mercado Livre na nuvem (GitHub Actions)

Roda toda segunda às 8h (Brasília), gera o relatório e os posts e envia tudo por e-mail,
sem precisar de computador ligado.

## Passo 1: repositório PRIVADO

Crie um repositório **privado** no GitHub e envie estes arquivos para ele, mantendo a estrutura:

```
relatorio_semanal_ml.py
postar_redes.py
custos.csv                  <- edite com seus produtos
.gitignore
.github/workflows/relatorio-semanal.yml
```

Importante: privado, porque o `custos.csv` mostra suas margens.

## Passo 2: autorizar o app do ML (uma vez, no seu computador)

Siga o passo "CONFIGURAÇÃO" no topo do script e rode `python3 relatorio_semanal_ml.py --auth TG-xxxx`.
Isso cria o `tokens.json`. Abra-o e copie o valor de `refresh_token`.

**Atenção:** depois de copiar, NÃO rode o script localmente de novo com esse mesmo token.
Ele é de uso único. Se rodar, o token do secret fica inválido e você precisa refazer o `--auth`.

## Passo 3: criar o token do GitHub (GH_PAT)

O workflow precisa regravar o refresh token a cada semana. Para isso:

1. GitHub > Settings > Developer settings > Personal access tokens > **Fine-grained tokens** > Generate.
2. Em "Repository access" escolha **Only select repositories** e marque só este repositório.
3. Em "Repository permissions" dê **Secrets: Read and write**. Nenhuma outra.
4. Defina a validade (ex.: 1 ano) e anote a data para renovar.
5. Copie o token gerado.

## Passo 4: cadastrar os secrets

No repositório: Settings > Secrets and variables > Actions > New repository secret.

| Secret | Valor |
|---|---|
| `ML_APP_ID` | App ID do app no ML |
| `ML_CLIENT_SECRET` | Secret Key do app |
| `ML_REDIRECT_URI` | A URI de redirecionamento cadastrada |
| `ML_REFRESH_TOKEN` | O `refresh_token` do passo 2 |
| `GH_PAT` | O token do passo 3 |
| `SMTP_HOST` | Ex.: `smtp.gmail.com` |
| `SMTP_PORT` | `465` |
| `SMTP_USER` | Seu e-mail de envio |
| `SMTP_PASS` | "Senha de app" do Google (não a senha normal) |
| `EMAIL_FROM` | Opcional: remetente exibido |
| `EMAIL_DESTINO` | Quem recebe (vários: separe por vírgula) |

## Passo 5: testar

Aba **Actions** > "Relatório semanal Mercado Livre" > **Run workflow**.
Em poucos minutos o e-mail chega, e os arquivos ficam em "Artifacts" na página da execução
por 30 dias.

## Problemas comuns

- **`invalid_grant` / erro 400 ao renovar:** o refresh token do secret é inválido (já foi usado
  fora do workflow, ou expirou). Refaça o Passo 2 e atualize `ML_REFRESH_TOKEN`.
- **Falha no passo "Salvar o novo refresh token":** o `GH_PAT` expirou ou não tem permissão
  "Secrets: Read and write". Se isso falhar, a próxima execução quebra. Corrija e refaça o Passo 2.
- **Não rodou na segunda:** o GitHub pode atrasar execuções agendadas em alguns minutos, e
  desativa agendamentos em repositórios sem atividade por 60 dias. Basta reativar em Actions.
- **Horário de verão:** o Brasil não usa mais, então 11:00 UTC = 8h em Brasília o ano todo.

---

# Parte 2: publicar os posts no Instagram e Facebook

A cada execução o relatório gera `posts_AAAA-MM-DD.txt` (para você ler) e `.json` (para publicar):
um **carrossel** com os 3 mais vendidos e **um post por produto**, com foto, preço e legenda.
Instagram: "link na bio" (links em legenda não são clicáveis). Facebook: link do anúncio na legenda.

**Por padrão NADA é publicado.** Você recebe os textos por e-mail e só ativa a publicação quando
confiar no resultado.

## Como ativar (recomendado, em 3 etapas)

1. **Semanas 1-2:** só relatório por e-mail. Leia os posts e veja se a qualidade serve.
2. **Publicação manual:** Actions > Run workflow > marque **Publicar posts**. Confira o resultado nas redes.
3. **Automático:** Settings > Secrets and variables > Actions > aba **Variables** > crie
   `PUBLICAR_POSTS` = `true`. Para pausar, apague a variável ou troque para `false`.

## Configuração da Meta (uma vez)

1. A conta do Instagram precisa ser **profissional** (Comercial ou Criador) e estar vinculada a uma
   **Página do Facebook**.
2. Em developers.facebook.com crie um app do tipo **Business** e adicione o produto
   **Instagram** (API com login do Facebook).
3. No **Graph API Explorer**, gere um token de usuário com as permissões:
   `instagram_basic`, `instagram_content_publish`, `pages_show_list`,
   `pages_read_engagement`, `pages_manage_posts` (esta última só para postar no Facebook).
4. Troque-o por um token de longa duração:
   `GET https://graph.facebook.com/v25.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=TOKEN_CURTO`
5. Com o token de longa duração, chame `GET /me/accounts`. A resposta traz, para cada Página, o
   `id` (= **FB_PAGE_ID**) e o `access_token` da Página (= **META_ACCESS_TOKEN**).
   O token de Página obtido assim normalmente não expira; confira no *Access Token Debugger*.
6. Descubra o ID do Instagram: `GET /FB_PAGE_ID?fields=instagram_business_account&access_token=...`
   O `id` dentro de `instagram_business_account` é o **IG_USER_ID**.

Enquanto o app estiver em modo de desenvolvimento, ele funciona nas contas em que você tem função
no app, normalmente sem passar pela App Review da Meta (confirme no painel do app).

Cadastre nos **Secrets**: `META_ACCESS_TOKEN`, `IG_USER_ID` e `FB_PAGE_ID`
(deixe `FB_PAGE_ID` ou `IG_USER_ID` em branco/inexistente para pular aquela rede).

## Testar localmente (sem postar)

```
export IG_USER_ID=123 FB_PAGE_ID=456
python3 postar_redes.py relatorios/posts_2026-11-02.json          # simulação
python3 postar_redes.py relatorios/posts_2026-11-02.json --publicar --somente instagram --limite 1
```

## Limitações e cuidados

- **Instagram só aceita imagem JPEG em URL pública.** O ML costuma servir fotos em `.webp`; o script
  troca para `.jpg` da mesma imagem, mas isso pode falhar em alguns anúncios. Nesse caso o post
  daquele produto falha (com a mensagem no log) e os demais seguem. A execução termina com erro
  para você ser avisado.
- **Rodar duas vezes publica duas vezes.** Não há controle de duplicidade: não use "Publicar"
  de novo para a mesma semana.
- **Foto do produto, não arte editada.** Os posts usam a foto do anúncio. Para artes com marca,
  o próximo passo seria gerar imagens com seu logo (precisa de hospedagem pública da imagem).
- Limite da API: cerca de 100 publicações a cada 24h. Aqui são 4 por rede por semana.
- O app e o token mudam de regras com o tempo. Se der erro de versão, defina a variável
  `META_API_VERSION` (o padrão é `v25.0`).
