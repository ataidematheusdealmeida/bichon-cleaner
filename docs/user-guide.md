# Guia de uso do Bichon Cleaner

O **Bichon arquiva** os e-mails. O **Cleaner verifica o arquivo e exclui no servidor de origem**, quando a regra autoriza. A cópia do Bichon permanece disponível. A exclusão no IMAP é definitiva: não envia para a lixeira.

Este guia descreve o comportamento do código publicado, não o estado atual de uma instalação. Confira também os [problemas conhecidos da revisão](code-review.md), principalmente antes de ampliar a exclusão automática.

## 1. Acessar

Abra o endereço configurado para o painel, por exemplo `http://SEU_SERVIDOR:15632`, pela rede administrativa. Entre com um usuário administrador do **Bichon**. Usuários comuns das caixas não podem acessar o Cleaner; a permissão exigida é `system:root`.

O Bichon Mail e o Cleaner são interfaces diferentes. As portas padrão são 15630 e 15632, respectivamente. A senha informada no login é enviada ao Bichon para autenticação; o painel guarda um token na sessão, não a senha.

Se voltar ao login, confira a autenticação do Bichon e a permissão administrativa. Se houver “conexão recusada”, o administrador deve conferir o serviço do painel e a porta. A [instalação](installation.md) explica os serviços necessários.

## 2. Entender os dois controles

Existem dois níveis independentes:

| Controle | Efeito |
| --- | --- |
| Regra ativa/pausada | Define se aquela política participa do processamento |
| Exclusão global ligada/desligada | Autoriza ou impede exclusões reais no conjunto de regras |

Uma regra ativa pode estar em **simulação**. Nesse modo o worker consulta servidor e arquivo, mas não apaga mensagens. Com a exclusão global ligada e a regra ativa em modo real, ela pode apagar ao satisfazer as verificações.

**Antes de ativar uma regra, leia o modo global.** Se ele já estiver ligado, o botão de ativação pode ativar a regra diretamente para exclusão. Criar e editar deixam a regra pausada/em simulação, mas ativá-la novamente adota o modo global vigente.

## 3. Criar uma regra

Em “Nova regra”, preencha:

1. **Conta:** escolha uma conta. “Todas as contas” amplia o escopo para todas as contas habilitadas conhecidas pelo Bichon.
2. **Pastas:** deixe vazio para abranger todas as pastas desse escopo, ou adicione uma ou mais tags. Uma regra global usa os nomes selecionados nas contas em que essas pastas existirem.
3. **Exceto:** marque para escolher uma ou mais pastas protegidas. Elas não devem entrar no processamento da regra. Se a regra abrange todas as pastas, isso inclui pastas como lixeira e enviados quando conhecidas pelo Bichon, a menos que você as exclua do escopo.
4. Configure idade e verificações conforme a tabela abaixo.
5. Clique **Criar regra pausada**. Confira o resumo antes de ativar.

| Campo | Exemplo | O que significa |
| --- | --- | --- |
| Mais antigos que, meses | `12` | Mensagens recebidas há mais de um ano; inclui mensagens de 2016, por exemplo |
| Recibo arquivado há, dias | `1` | O recibo individual deve existir há pelo menos 24 horas |
| Verificações mínimas | `2` | A mesma identidade e conteúdo precisam de duas verificações contabilizadas |
| Intervalo entre verificações, horas | `24` | Uma nova passagem só aumenta a contagem após esse intervalo |
| Mensagens por lote | `50` ou `200` | Quantidade máxima de candidatos processados por pasta em um ciclo normal |
| Executar a cada, horas | `24` | Intervalo entre ciclos automáticos de cada regra/pasta |

O padrão do formulário para idade do recibo é **14 dias**. Se a intenção é um dia, altere explicitamente para `1`. A data da mensagem vem de `INTERNALDATE` no IMAP; não é a data em que você criou a regra. O limite de meses é calculado em UTC.

“Arquivado há um dia” não significa “e-mail com um dia”: são condições diferentes. Uma mensagem pode ter cinco anos e ter sido arquivada ontem.

Evite duas regras com conjuntos de pastas parcialmente sobrepostos na mesma conta. A versão revisada aceita esse cadastro, mas pode falhar ao executar. Prefira uma regra por conta com exceções ou conjuntos sem interseção. Veja R3 no relatório.

## 4. Exemplo: todas as pastas de uma conta

Para manter no servidor apenas o período mais recente e liberar mensagens antigas já arquivadas:

| Parâmetro | Valor |
| --- | --- |
| Conta | A conta desejada |
| Pastas | Nenhuma tag, para todas as pastas |
| Exceto | Escolha somente as pastas que deseja preservar no servidor |
| Mais antigos que | 12 meses |
| Recibo arquivado há | 1 dia |
| Verificações mínimas | 2 |
| Intervalo entre verificações | 24 horas |
| Lote | 50 inicialmente |
| Executar a cada | 24 horas |

Crie a regra, confirme que a exclusão global está desligada e ative a simulação. Esse exemplo não cria nem altera automaticamente regras na sua instalação.

## 5. Executar uma simulação

Com a regra ativa e a exclusão global desligada, clique **Executar agora** na regra para processar somente aquele alvo. O botão geral **Executar agora · regras ativas** solicita o conjunto das regras ativas.

O pedido entra na fila. Com `bichon-cleaner.path` instalado e ativo, o systemd aciona o worker. Sem ele, o pedido depende do próximo disparo do timer. Um worker em andamento continua protegido por lock: vários cliques não significam várias execuções simultâneas.

O botão ignora o intervalo de agendamento, mas mantém idade do arquivo, intervalo entre verificações e lote. Se a primeira verificação foi às 10h e o intervalo é 24h, clicar às 10h05 não completa a segunda verificação contabilizada.

Mesmo com uma mensagem antiga, faltará autorização se o recibo for recente, ausente, incompatível ou apontar um arquivo indisponível.

## 6. Ler os resultados

| Indicador | Como interpretar |
| --- | --- |
| Antigas | Mensagens encontradas pela busca de idade no IMAP, antes de todas as proteções |
| Conferidas | Candidatos tentados nessa execução |
| Verificadas | Mensagens cuja validação passou nessa passagem; o intervalo pode impedir incremento da contagem persistente |
| Elegíveis | Passaram na validação e atingiram a quantidade mínima de verificações |
| Excluídas nesta execução | Expurgos com resposta bem-sucedida registrados pelo worker no ciclo exibido |
| Bloqueadas | Tentativas impedidas por erro ou condição de proteção |
| Excluídas no histórico | Confirmações acumuladas no arquivo de auditoria |

Exemplo: 5.000 antigas, 50 conferidas e zero excluídas pode ser normal no primeiro ciclo. “50 conferidas” não significa “a conta inteira já está arquivada”. Consulte cada pasta e seus impedimentos.

O painel atualiza os relatórios a cada dez segundos enquanto a aba está visível. **Atualizar contadores** consulta o estado; não inicia outra execução.

## 7. Ligar a exclusão real

Depois de conferir o resultado da simulação:

1. Abra **Ligar exclusão real** no painel de modo de execução.
2. Leia quais regras estão ativas: a liberação se aplica a elas.
3. Informe a senha do administrador atualmente conectado.
4. Marque a confirmação de exclusão definitiva e confirme.
5. Clique **Executar agora** na regra desejada, ou aguarde o ciclo automático.

Ligar a exclusão **não inicia uma execução por si só**. Se a mensagem já atingiu as verificações e demais condições, poderá ser excluída na execução solicitada. Caso contrário, continuará aguardando ou bloqueada. O arquivo do Bichon não é removido.

## 8. Alterar ou interromper

- **Pausar:** desativa a regra para o processamento subsequente. Há uma janela conhecida na mensagem que já está passando pela verificação final; veja R1. Não trate o clique como cancelamento instantâneo de uma operação IMAP já enviada.
- **Editar:** salva a configuração pausada/em simulação e incrementa a revisão. As verificações antigas não autorizam a nova revisão. Confira o modo global antes de reativar.
- **Excluir regra:** remove a política, não mensagens ou o histórico já registrado.
- **Desligar exclusão · voltar à simulação:** impede novas exclusões quando o worker relê a autorização global; uma operação já enviada pode terminar.

Na versão revisada, evite recarregar uma página confirmando o reenvio de formulário: isso pode repetir ativar/pausar. Navegue novamente pela URL do painel e confira o estado exibido.

## 9. Histórico e dúvidas frequentes

Em “E-mails excluídos do servidor”, veja as últimas 100 confirmações. Use **Baixar histórico completo (CSV)** para exportar os registros disponíveis. O CSV contém dados pessoais e assuntos; guarde em local restrito.

**Aumentar de 50 para 200 faz apagar mais rápido?** Pode aumentar o volume processado por pasta em cada ciclo, com maior uso de rede e disco. Com dez pastas elegíveis, o limite normal pode chegar a 2.000 candidatos por ciclo. Não reduz a espera de segurança nem garante 2.000 exclusões.

**Está apagando mensagens muito antigas. Isso está certo?** Sim: uma regra “mais de 12 meses” inclui qualquer mensagem anterior ao limite, não apenas as que completaram exatamente um ano.

**Foi para a lixeira?** Não. O Cleaner marca o UID e faz expurgo direcionado no servidor IMAP. A cópia para consulta fica no Bichon.

**O contador do Cleaner mudou, mas o espaço do provedor não?** O contador registra confirmações de expurgo, não o uso físico calculado pelo provedor. A atualização do espaço depende do servidor de e-mail.

**Parece parado. O que conferir?** Veja estado da execução, data da atualização, impedimentos por pasta e intervalos configurados. O administrador pode consultar os [comandos de diagnóstico](operations.md). Não edite contadores ou recibos para forçar uma aprovação.
