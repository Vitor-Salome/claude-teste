# Radar semanal de mercado: Mercado Livre

Toda segunda às 8h (Brasília) você recebe por e-mail um relatório com:

- **Nichos mais fortes** (categorias do Mercado Livre): vendas na semana, preço mediano, quantos vendedores
  dominam o top 20, % com frete grátis e % no Full.
- **Produtos mais vendidos** em geral, com foto, preço e link.
- **Novidades:** produtos que entraram no ranking e quem subiu ou caiu de posição.
- **Buscas em alta** no Mercado Livre (quando disponível).
- Anexos: relatório completo em HTML (ranking de cada nicho) e planilha CSV.

Não usa suas vendas nem exige conta de vendedor: lê os rankings públicos de "mais vendidos" da API oficial.

## Configuração (uma vez, uns 20 minutos)

### 1. Criar o app no Mercado Livre
1. Entre em **developers.mercadolivre.com.br** com uma conta do Mercado Livre (não precisa vender nada).
2. **Minhas aplicações > Criar nova aplicação**. Nome e descrição livres.
3. **URL de redirecionamento:** `https://localhost/callback` (não será usada).
4. Permissões: só leitura. Salve e anote o **App ID** e a **Secret Key**.

### 2. Criar a senha de app do Gmail
Conta Google > Segurança > ative a **Verificação em duas etapas** > **Senhas de app** > crie uma e copie
as 16 letras. Não use a senha normal do Gmail.

### 3. Cadastrar os Secrets
No repositório: **Settings > Secrets and variables > Actions > New repository secret**.

| Secret | Valor |
|---|---|
| `ML_APP_ID` | App ID do passo 1 |
| `ML_CLIENT_SECRET` | Secret Key do passo 1 |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `465` |
| `SMTP_USER` | seu Gmail |
| `SMTP_PASS` | a senha de app do passo 2 |
| `EMAIL_DESTINO` | quem recebe (vários: separe por vírgula) |

### 4. Testar
**Actions > Radar semanal de mercado > Run workflow.** Em alguns minutos o e-mail chega. Os arquivos também
ficam em "Artifacts" na página da execução por 30 dias.

Depois do primeiro teste a rotina é automática. Se quiser nichos mais específicos (subcategorias), crie em
Settings > Secrets and variables > Actions > **Variables** a variável `RADAR_NIVEL` com valor `2`
(demora mais e faz mais chamadas à API; teste com o nível 1 primeiro).

## Como ler o relatório

- **Vendas na semana\*:** diferença das vendas acumuladas entre esta segunda e a anterior. É uma
  **estimativa**; o Mercado Livre pode arredondar esses números. Só aparece a partir da **2ª semana**.
- **Vendas acum. (top 20):** vendas totais dos 20 produtos do ranking. Mostra o tamanho do nicho, não o ritmo.
- **Vendedores distintos:** poucos = nicho concentrado (difícil de entrar). Muitos = mercado pulverizado.
- **Full:** produtos que usam o armazém do Mercado Livre. Alto % indica que a concorrência já tem logística forte.
- O ranking de "mais vendidos" é calculado pelo próprio Mercado Livre.

## Limitações e problemas comuns

- **Erro 401/403 ao buscar o ranking:** o Mercado Livre pode exigir autorização de uma conta de usuário para
  esse endpoint, e não aceitar só o app. O script avisa isso claramente. Se acontecer, me mande a mensagem
  do log que eu adapto (a versão com autorização de usuário já existe em `fase2-minhas-vendas/`).
- **Categorias sem ranking** aparecem listadas como aviso no relatório. É normal para algumas.
- O histórico semanal é gravado na pasta `dados/` pelo próprio workflow. Não apague, ele é a base da comparação.
- O GitHub desativa agendamentos de repositórios sem atividade por 60 dias; aqui o histórico semanal conta como
  atividade, então não deve ocorrer.
- Enquanto o repositório for público, qualquer pessoa vê os relatórios e o histórico (são dados públicos
  de mercado). Os Secrets nunca ficam visíveis. Se preferir, deixe o repositório privado.

## Depois: quando você começar a vender
A pasta `fase2-minhas-vendas/` tem o relatório das suas próprias vendas, o cálculo de lucro e a publicação de
posts no Instagram/Facebook. Está desativada; veja `COMO-ATIVAR.md` lá dentro.
