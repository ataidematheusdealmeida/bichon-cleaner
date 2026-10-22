# Operação e diagnóstico

## Regras

Selecione uma conta ou todas as contas. Pastas sem seleção significa todas as pastas conhecidas no Bichon para o escopo. Selecione várias tags para limitar as pastas. Marque “Exceto” e escolha tags para proteger pastas.

Uma regra específica por pasta tem prioridade sobre uma regra por conta; uma regra por conta tem prioridade sobre a global. Regras pausadas mais específicas impedem o fallback. Exceções de regras ativas no escopo têm efeito de veto. Conflitos de mesma prioridade bloqueiam a seleção.

Criar ou editar deixa a regra **pausada e em simulação**. Editar incrementa sua revisão: as verificações da revisão anterior deixam de autorizar exclusões nessa regra. Excluir uma regra remove somente a configuração; o histórico é mantido.

| Campo | Significado |
| --- | --- |
| Mais antigos que, meses | Limite de idade da mensagem, calculado por meses de calendário em UTC |
| Recibo arquivado há, dias | Idade mínima do recibo individual no Bichon; mínimo 1 dia |
| Verificações mínimas | Número de verificações aprovadas; mínimo 2 |
| Intervalo entre verificações, horas | Tempo entre verificações que incrementam o contador; mínimo 1 hora |
| Mensagens por lote | Limite de candidatos processados **por pasta e por execução**, de 1 a 500 |
| Agendamento, horas | Intervalo normal entre ciclos da regra por pasta |

Aumentar 50 para 200 processa até quatro vezes mais candidatos por pasta em cada ciclo, com mais tráfego e leitura. Isso não reduz a idade mínima nem dispensa verificações.

## Simulação, exclusão e execução imediata

1. Ative a regra e confira uma simulação.
2. Libere a exclusão no botão do painel: exige confirmação e a senha do administrador da sessão.
3. Clique “Executar agora” para colocar a execução na fila.

Liberar exclusão altera as regras ativas para `dry_run=false`, mas não inicia o worker. Uma regra criada ou editada depois continua pausada/em simulação; revise o modo antes de esperar exclusões.

“Executar agora” ignora o agendamento do ciclo, **não o intervalo entre verificações nem a idade do arquivo**. Cliques repetidos não criam vários pedidos se já existe um na fila. Desativar exclusão volta para simulação; pausar/excluir/editar uma regra também interrompe seu processamento quando o worker relê a configuração.

O comando `worker.py --force` tem o mesmo efeito de ignorar o agendamento. `--apply` ainda exige a liberação global. Sem `--apply`, o worker também exclui se a liberação global estiver ativa e a regra permitir: não use a ausência desse argumento como garantia de simulação.

### Exceção operacional herdada

O código contém suporte ao campo interno `recheck_existing_now=true` em `state/run-request.json`, somente quando há `rule_id`. Ele revalida apenas identidades já verificadas antes dessa execução, **dispensa a espera entre verificações e ignora o tamanho do lote**. Ainda exige idade do arquivo, nova validação completa, número mínimo de verificações e autorização de exclusão. O painel não cria esse campo; não o utilize em rotinas normais nem o confunda com “Executar agora”.

## Contadores e histórico

`state/latest-report.json` mostra candidatos antigos, processados, verificados, prontos, excluídos e bloqueados por pasta. “Antigos” é o resultado da busca IMAP, não a quantidade que será excluída. Os números do ciclo não provam que toda a caixa foi arquivada.

`state/audit.jsonl` registra `delete_requested` antes do expurgo e `remote_deleted` depois da resposta bem-sucedida do servidor. O painel conta somente confirmações e mostra os 100 registros mais recentes; o CSV exporta as confirmações disponíveis no arquivo. Assunto, conta, pasta, UID e data podem aparecer na auditoria.

Se houver interrupção após o servidor excluir e antes da confirmação ser gravada, pode existir apenas `delete_requested`. Não reclassifique manualmente como confirmado sem conferir o servidor. O contador não mede o espaço físico já recuperado pelo provedor.

## Se não estiver excluindo

- Confira regra ativa, `dry_run=false` e liberação global ligada.
- Consulte `state/execution-status.json`, `latest-report.json` e `journalctl -u bichon-cleaner.service`.
- Pedido na fila: verifique `bichon-cleaner.path` e timer. “Running” sem progresso pode ser espera de rede; não remova o lock para iniciar outro processo.
- Verificado, mas não pronto: falta outra verificação ou o intervalo mínimo.
- Bloqueado: confira exemplos do relatório; recibo ausente, arquivo recente, hash/tamanho divergentes, download indisponível e UIDVALIDITY alterado impedem exclusão.
- UIDPLUS ausente, OAuth ou proxy: esse worker não exclui nessa configuração.
- Nenhuma pasta: confira contas habilitadas, escopo da regra e o snapshot/WAL do Bichon.

Não altere os contadores de estado para forçar aprovação. Exclusões usam o servidor IMAP configurado na conta; o Cleaner não move mensagens para a lixeira e não exclui o arquivo do Bichon.
