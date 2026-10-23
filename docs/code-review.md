# Revisão técnica — 22/10/2026

Código originalmente avaliado: commit `aad0facc6c804c8de310a8b501b5a587babd0dc4`. Os achados abaixo descrevem essa versão. Veja a atualização a seguir para o estado atual. Nenhum serviço de produção foi alterado nesta revisão ou na implementação local das correções.

## Atualização de 23/10/2026

| Achado | Estado atual |
| --- | --- |
| R1 | Regra, revisão e modo relidos depois da validação final e antes de chamar o expurgo. A pausa ainda não cancela uma operação já autorizada/enviada nem é transacional entre processos. |
| R2 | Tamanho validado antes da leitura, cabeçalhos inválidos/negativos rejeitados e timeout de conexão de 15 segundos. Proxy/firewall e controle de taxa ainda necessários. |
| R3 | Novas sobreposições na mesma prioridade por conta rejeitadas. Conflitos legados são registrados por pasta e não abortam as demais contas. |
| R4 | Pedido assumido preservado em arquivo de processamento; falhas fatais preservadas em arquivo de falha, sem repetição automática. O operador pode solicitar nova execução após conferir a causa. |
| R5 | Ativar/pausar envia o estado desejado. Ações bem-sucedidas usam redirecionamento 303 e mensagem na página seguinte. |
| R6 | Atalho interno removido; o campo legado não dispensa mais intervalo nem lote. |
| R7 | CSRF ligado à sessão por HMAC com chave privada; origem conferida quando enviada; permissão administrativa revalidada também na página principal; expiração fixa de 12 horas e opção de cookie Secure. Limitação de tentativas ainda deve ser tratada no proxy/autenticação. |
| R8 | Continua dependente da integração externa do Bichon. Não foram incluídos patch de recibos, matriz de versões ou migração de esquema. |

A variável sem uso foi removida. As cinco reproduções originais em `tools/review_probes.py` agora exigem resultados seguros, e testes adicionais verificam CSRF, expiração, permissão revogada, cabeçalhos inválidos, continuidade após conflito e manutenção de intervalo/lote com o campo legado.

Validação da versão corrigida: **28 testes aprovados**, incluindo um teste HTTP em loopback para renderização do token da sessão, redirecionamento 303 e repetição idempotente da ativação. A compilação dos módulos Python também passou.

Instalações existentes precisam receber e reiniciar explicitamente o código atualizado. Nenhuma exclusão real, restauração ou execução systemd em Linux foi testada nesta etapa. As recomendações de acessibilidade, desempenho do histórico e compatibilidade permanecem como melhorias futuras.

## Resultado

Foram reproduzidos cinco problemas com dados fictícios e serviços simulados. Os 19 testes existentes passaram, mas não cobrem esses cenários. Recomenda-se corrigir os itens P1 antes de ampliar o uso da exclusão automática.

| ID | Prioridade | Problema | Evidência |
| --- | --- | --- | --- |
| R1 | P1 — alta | Pausar/editar/excluir durante a verificação final pode não impedir a exclusão em andamento | Reprodução isolada |
| R2 | P1 — alta | Limite HTTP aplicado depois de ler o corpo permite consumo excessivo de recursos | Reprodução com leitor fictício |
| R3 | P1 — alta | Regras com pastas sobrepostas são aceitas e podem abortar o ciclo inteiro | Reprodução isolada |
| R4 | P2 — média | Pedido de execução é consumido antes de operações que podem falhar | Reprodução isolada |
| R5 | P2 — média | Reenviar o POST de ativar/pausar desfaz a ação anterior | Reprodução da função e inspeção HTTP |
| R6 | P2 — média | Opção interna dispensa intervalo de segurança e limite de lote | Inspeção do fluxo |
| R7 | P2 — média | Proteção de sessão/CSRF precisa de reforço | Inspeção do fluxo |
| R8 | P2 — média | Dependência interna do Bichon limita instalação e atualização | Inspeção da integração |

P1 significa tratar antes da expansão operacional; P2 significa planejar no próximo ciclo. A classificação considera exclusões irreversíveis e o acesso restrito esperado para este painel, não representa uma pontuação CVSS.

## R1 — janela entre autorização e expurgo

Em [worker.py](../worker.py#L265), a regra é relida antes da segunda chamada de `verify()`. Depois dessa chamada, da consulta de envelope e da escrita de auditoria, o código verifica somente a liberação global e chama `erase()`. Uma pausa, edição ou exclusão ocorrida durante essas operações não é conferida novamente para a mensagem atual.

Na reprodução, a segunda verificação altera a regra para `enabled=false`, mas o expurgo simulado ainda é chamado uma vez. Isso não demonstra exclusão de mensagem sem arquivo; demonstra que a pausa individual pode chegar tarde para a mensagem em andamento.

**Correção:** reler regra, revisão, modo e autorização global imediatamente antes de autorizar o expurgo. Definir a semântica de pausa: uma operação IMAP já enviada pode concluir. Para uma garantia mais forte, coordenar essa fronteira com as mutações do painel; uma releitura de arquivo, sozinha, ainda tem uma pequena janela concorrente. Testar pausa, edição e exclusão durante as operações de rede finais.

## R2 — corpo HTTP lido antes do limite

[dashboard.py](../dashboard.py#L215) executa `rfile.read(size)` antes de rejeitar `size>65536`, antes também de autenticar a sessão. A reprodução fornece `Content-Length=1000000000` a um leitor fictício e confirma a tentativa de leitura desse tamanho antes do HTTP 413. Nenhum gigabyte real foi alocado no teste.

**Correção:** validar formato, sinal e limite de `Content-Length` antes de ler; limitar tempo de leitura/conexões e proteger o acesso com proxy/firewall. Não é necessário trocar todo o framework para corrigir a ordem dessa validação. A [documentação oficial do Python](https://docs.python.org/3/library/http.server.html) também alerta que `http.server` fornece apenas verificações básicas de segurança.

## R3 — conflito aceito no cadastro e fatal no worker

[save_rule()](../dashboard.py#L52) impede somente alvos exatamente iguais. Para uma mesma conta, permite uma regra com `INBOX, Sent` e outra com `INBOX, Protected`. Ao avaliar `INBOX`, [policy()](../worker.py#L45) encontra duas regras de mesma prioridade e lança exceção.

A primeira seleção de política em [run()](../worker.py#L225) fica fora do `try` por pasta: o erro pode encerrar o ciclo e impedir o processamento das demais contas. Inclusive regras pausadas participam da seleção e podem conflitar.

**Correção:** rejeitar sobreposições ambíguas no cadastro/edição, respeitando a prioridade intencional entre regras globais, de conta e de pasta. Registrar um erro por pasta e continuar as demais, sem excluir no escopo ambíguo.

## R4 — pedido perdido após falha

[run()](../worker.py#L203) remove `run-request.json` antes de carregar banco e concluir o ciclo. Uma falha de leitura do snapshot/WAL deixa o pedido indisponível; o próximo timer pode fazer um ciclo normal, mas perde o escopo e a intenção de execução forçada.

**Correção:** mover atomicamente o pedido para um estado “em processamento”, confirmar conclusão e manter falha recuperável com identificador e limite de tentativas. Mostrar o erro e permitir reenfileirar. Não recolocar pedidos indefinidamente em um `.path` ativo, para evitar repetição contínua de falhas.

## R5 — ativar/pausar não é idempotente

[toggle_rule()](../dashboard.py#L75) inverte o estado recebido do disco. [do_POST()](../dashboard.py#L239) responde com uma página 200, sem redirecionamento após a ação. Recarregar e aceitar o reenvio do formulário pode executar o mesmo POST novamente e reverter a pausa/ativação. A reprodução confirma a inversão em duas chamadas iguais.

**Correção:** enviar o estado desejado (`enabled=true/false`) e usar POST → 303 → GET com mensagem de resultado. A mesma revisão deve evitar reenvio acidental de outros formulários.

## R6 — exceção interna reduz garantias operacionais

`recheck_existing_now`, em [worker.py](../worker.py#L209), permite dispensar a espera entre verificações e processar todos os candidatos já verificados, ignorando o lote. Exige `rule_id` e não é enviado pelo painel; ainda exige recibo, conteúdo válido e autorização global.

**Melhoria:** retirar essa exceção da rotina distribuída ou torná-la uma operação excepcional com autenticação recente, justificativa e auditoria explícita. Não a apresentar como equivalente ao botão “Executar agora”. Enquanto existir, proteger a escrita no diretório de estado.

## R7 — reforço da sessão administrativa

O token CSRF em [dashboard.py](../dashboard.py#L12) é único para o processo e aparece na página pública de login e em todos os formulários. Logo, não comprova vínculo com uma sessão específica. `SameSite=Strict` oferece uma proteção adicional, mas não substitui esse vínculo. Não foi reproduzida uma exploração entre sites nesta revisão.

**Melhoria:** token CSRF por sessão, validação de origem quando apropriada, cookie `Secure` quando servido por HTTPS e política de expiração absoluta além da inatividade. A página principal também deve revalidar `system:root`, como já fazem `/report`, CSV e ações administrativas. Não há limitador de tentativas de login no Cleaner; aplicar controle no proxy e no serviço de autenticação.

## R8 — integração incompleta como distribuição independente

O Cleaner lê snapshot/WAL e credenciais criptografadas do Bichon diretamente. Depende ainda do endpoint customizado de recibos, cujo código não está neste repositório. Há caminho fixo da chave e UID/GID 10001 no worker. Portanto, clonar este projeto e instalar um Bichon oficial não garante uma instalação funcional.

**Melhoria:** publicar separadamente o patch/contrato versionado de integração, criar uma verificação de compatibilidade sem exclusão e testar atualização do Bichon. Parametrizar caminho da chave e propriedade dos arquivos quando houver necessidade de outro host. Preferir uma API consistente para contas/pastas a uma leitura de snapshot/WAL concorrente, quando a integração oferecer isso.

## Interface e operação

Revisão estática, sem ensaio visual em navegador nem avaliação completa de acessibilidade:

- A atualização substitui todo `execution-report` por `innerHTML` a cada dez segundos. Isso pode remover o elemento focado. Atualizar somente os valores necessários e anunciar um resumo por `role=status`, sem criar uma região viva para cada contador.
- Diferenciar “mensagem verificada nesta passagem” de “verificação que incrementou a contagem”. Atualmente uma passagem antes do intervalo pode contar como verificada no relatório sem aumentar o contador persistente.
- Mostrar próxima execução, próxima verificação elegível, escopo realmente visitado e motivo de espera. Isso responde diretamente à dúvida “por que ainda não apagou?”.
- O botão “Ativar exclusão” pode aparecer com a mensagem genérica “Estado da simulação atualizado”. A confirmação deve refletir a operação efetiva.
- O histórico é relido integralmente em cada atualização e o CSV é montado inteiro em memória. Medir com um histórico grande e, quando necessário, paginar/exportar por streaming e manter contadores incrementais.

## Simplificação — Ponytail Audit

1. `delete:` remover a variável local `mailbox=None`, sem uso em `save_rule()`. Substituição: nenhuma. [dashboard.py](../dashboard.py#L62)

net: -1 linha, -0 dependências possíveis nessa remoção confirmada.

A arquitetura já usa biblioteca padrão para o painel e duas dependências justificadas no worker. Não há motivo identificado para introduzir um framework, uma fila externa ou camadas genéricas apenas para corrigir os achados acima. Consolidar as regras CSS repetidas é uma melhoria de manutenção, mas exige conferir as propriedades ainda fornecidas pelo bloco original.

## Validação e limites

Skills aplicados: Graphify (estrutura e relações), Ponytail/Ponytail Audit (complexidade) e UI/UX Pro Max (interação e acessibilidade). Skills de documentos Office, imagens raster, apresentações e outros domínios não acrescentam validação ao código deste projeto.

- Graphify: extração local somente de código, 113 nós, 234 relações e 12 comunidades; diagnóstico pós-construção sem endpoints ausentes/soltos ou relações colapsadas detectadas. O grafo orienta a leitura; não prova correção.
- `python -m unittest discover -p 'test_*.py' -v`: 19 testes aprovados.
- `python tools/review_probes.py`: cinco reproduções confirmadas. O script usa diretórios temporários e mocks para Bichon, IMAP e expurgo; não deve ser confundido com cinco correções aprovadas.
- Não houve teste de exclusão real, restauração, criptografia real, carga, versões do Bichon ou unidades systemd em Linux nesta revisão. Não foi feito levantamento de vulnerabilidades das versões instaladas das dependências.

As correções devem transformar as reproduções em testes que exigem o comportamento correto. Ordem sugerida: R1/R2/R3, depois R4/R5/R6, seguida de sessão, compatibilidade e clareza operacional.
