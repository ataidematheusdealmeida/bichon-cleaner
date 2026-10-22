# Instalação no Linux

Esta instalação usa dois serviços Python e recursos nativos do systemd. Não inclui nem instala o Bichon. Primeiro confira a [integração obrigatória](bichon-integration.md). Não substitua arquivos de uma instalação existente por estes exemplos: eles não contêm regras nem histórico de produção.

## 1. Código e dependências

Execute como root no host confiável do Bichon. O clone de um repositório privado exige sua autenticação GitHub previamente configurada.

```bash
git clone https://github.com/ataidematheusdealmeida/bichon-cleaner.git /opt/bichon-cleaner
cd /opt/bichon-cleaner
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

O worker requer Linux (`fcntl`), Python 3.12+, `cryptography` e `blake3`. O painel usa a biblioteca padrão. As dependências não estão travadas em um lockfile; valide atualizações antes de aplicá-las em produção.

## 2. Diretórios e usuário do painel

O código usa UID/GID 10001 ao gravar estado como root. Se esse ID já pertencer a um usuário no host, revise a associação e a confidencialidade antes de continuar; não reutilize uma conta de outra aplicação sem avaliar seus acessos.

```bash
groupadd --gid 10001 bichon-cleaner
useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin bichon-cleaner
install -d -o 10001 -g 10001 -m 0700 config state
install -d -o root -g root -m 0700 secrets
install -o 10001 -g 10001 -m 0600 config/rules.example.json config/rules.json
install -o 10001 -g 10001 -m 0600 config/execution.example.json config/execution.json
```

As regras começam vazias e a exclusão desativada. Não altere a propriedade de todo o checkout: o painel não precisa modificar código ou unidades de serviço.

## 3. Acesso ao Bichon

Configure um token administrativo válido em `/opt/bichon-cleaner/secrets/api-token`, com proprietário root e modo `0600`, por um editor ou gerenciador de segredos. Não coloque o token em comandos, Git ou documentação.

Alternativa: o worker aceita a senha do usuário `admin` em `secrets/admin-password`, também `0600`. O token tem preferência se ambos existirem. O painel pede as credenciais pessoais do administrador e exige `system:root`.

Defaults do worker:

| Variável/caminho | Valor |
| --- | --- |
| `CLEANER_ROOT` | `/opt/bichon-cleaner` |
| `BICHON_DATA` | `/opt/bichon/data` |
| `BICHON_URL` | `http://127.0.0.1:15630` |
| Chave de criptografia, caminho fixo | `/opt/bichon/secrets/encrypt-password` |

O painel usa `CLEANER_ROOT=/opt/bichon-cleaner/config` e `CLEANER_STATE=/opt/bichon-cleaner/state`. Essa diferença entre os dois processos é intencional. Ajuste as unidades se os caminhos do host forem diferentes; a chave exige também ajustar `worker.py` nesta versão.

## 4. Serviços

```bash
install -m 0644 deploy/bichon-cleaner*.service deploy/bichon-cleaner*.timer deploy/bichon-cleaner*.path /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now bichon-cleaner-dashboard.service bichon-cleaner.timer bichon-cleaner.path
systemctl status bichon-cleaner-dashboard.service bichon-cleaner.timer bichon-cleaner.path
```

O painel escuta em `0.0.0.0:15632`, sem TLS próprio. Restrinja o acesso por firewall à rede administrativa ou use um proxy com HTTPS. Não exponha essa porta diretamente à internet.

O timer procura trabalho a cada 15 minutos; a regra ainda controla o intervalo entre ciclos. O `.path` observa pedidos do botão “Executar agora”. Sem esse serviço, o pedido aguarda o próximo timer. O worker usa lock para impedir duas execuções simultâneas.

## 5. Primeira validação

Entre no painel, crie uma regra para uma caixa de teste, ative a simulação e use “Executar agora”. Confira:

```bash
journalctl -u bichon-cleaner.service -n 100 --no-pager
cat state/latest-report.json
```

Valide o arquivo e uma recuperação da caixa de teste antes de liberar exclusões. A simulação lê mensagens e arquivos para verificar, mas não executa `UID EXPUNGE`. O token, o acesso local, os recibos e a configuração IMAP precisam estar funcionais mesmo em simulação.

## Atualização e backup

Preserve `config/`, `state/` e `secrets/`, além dos backups independentes do Bichon. Pare timer/path e aguarde o worker terminar antes de copiar o estado ou atualizar o código. Pare também o painel durante o backup para evitar alterações concorrentes. O histórico pode conter assuntos e endereços de e-mail: proteja também suas cópias.

Não há rotação de auditoria nem migração automática de estado nesta versão. Planeje espaço em disco e retenção dos logs com o serviço parado.
