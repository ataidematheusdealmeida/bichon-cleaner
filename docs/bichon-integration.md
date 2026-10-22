# Integração obrigatória com o Bichon

O Cleaner depende da API e do formato interno de armazenamento da instalação integrada do Bichon. **Não é um cliente IMAP independente.** A extensão de recibos abaixo precisa estar implementada no Bichon e preenchida durante o arquivamento. Essa extensão e eventuais rotinas de preenchimento retroativo não estão incluídas neste repositório.

Não há promessa de compatibilidade com todas as versões oficiais. Antes de atualizar o Bichon, valide os endpoints, o formato do snapshot/WAL e a descriptografia em ambiente de teste. Não remova as verificações para contornar incompatibilidades.

## API utilizada

| Endpoint | Uso |
| --- | --- |
| `POST /api/login` | Autenticação |
| `GET /api/v1/current-user` | Validação da sessão e permissão administrativa no painel |
| `GET /api/v1/minimal-account-list` | Contas disponíveis |
| `GET /api/v1/list-mailboxes/{account_id}?remote=false` | Pastas conhecidas |
| `GET /api/v1/archive-receipt/{account_id}/{mailbox_id}/{uid}/verify` | Confirmação do arquivamento, extensão obrigatória |
| `GET /api/v1/download-message/{account_id}/{envelope_id}` | Leitura e reconstrução do arquivo |
| `GET /api/v1/envelope/{account_id}/{envelope_id}` | Assunto para auditoria |

O worker não chama endpoints de exclusão do Bichon.

## Contrato do recibo

Resposta ilustrativa, com identificadores fictícios:

```json
{
  "verified": true,
  "envelope_indexed": true,
  "email_blob_present": true,
  "attachment_blobs_present": true,
  "receipt": {
    "account_id": 1,
    "mailbox_id": 10,
    "uid": 123,
    "uid_validity": 456,
    "mailbox_name": "INBOX",
    "original_size": 1024,
    "ingested_at": 1700000000000,
    "content_hash": "BLAKE3_HEXADECIMAL_DO_EML_ORIGINAL",
    "envelope_id": "IDENTIFICADOR_DO_ENVELOPE"
  }
}
```

Todos os quatro indicadores precisam ser booleanos `true`. Os identificadores devem manter o tipo utilizado no banco e no IMAP. `ingested_at` usa milissegundos Unix. O hash deve corresponder aos bytes originais completos obtidos por `BODY.PEEK[]`, antes de qualquer reconstrução ou normalização.

O endpoint deve confirmar a identidade original e a disponibilidade dos blobs do corpo e dos anexos. O Cleaner também lê o download reconstruído e rejeita arquivo vazio. Ele não compara o hash do download reconstruído ao original, pois essa reconstrução pode mudar a representação MIME.

Mensagens sem recibo correspondente, com hash divergente ou blobs indisponíveis são bloqueadas. “Sincronização concluída” da conta não substitui o recibo individual.

## Acesso local

O worker lê `BICHON_DATA/memdb/snapshot.json` e `wal.jsonl`, aplica as operações de contas/pastas posteriores ao snapshot e usa as credenciais IMAP já cadastradas. A leitura direta pressupõe os esquemas esperados pelo código; não há migração automática nem API de snapshot consistente. Um WAL incompleto pode bloquear a execução e exigir nova tentativa.

A senha IMAP é descriptografada usando a chave em `/opt/bichon/secrets/encrypt-password`, PBKDF2-HMAC-SHA256 e AES-GCM. Esse caminho é fixo nesta versão. A chave e os dados devem ser acessíveis apenas no host confiável. A senha IMAP não é exibida no painel.

Os serviços sugeridos executam o worker como root para ler esses arquivos e o painel como UID/GID 10001. O painel recebe somente diretórios de configuração/estado, sem precisar ler os dados e a chave do Bichon.
