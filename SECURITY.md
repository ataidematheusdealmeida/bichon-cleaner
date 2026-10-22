# Segurança

Este software pode excluir definitivamente mensagens do servidor IMAP. A cópia preservada no Bichon precisa de backup e de testes de recuperação independentes.

- Mantenha o painel restrito à administração e protegido por HTTPS no proxy quando usado fora de loopback/rede confiável. Ele usa o servidor HTTP padrão do Python e não inclui rate limiting de autenticação.
- Acesso ao painel exige a permissão `system:root` do Bichon. A liberação de exclusão exige nova autenticação com senha.
- Token administrativo, senha do Bichon e chave de criptografia ficam fora do Git, com acesso mínimo no host.
- O worker lê dados internos e descriptografa credenciais IMAP; execute somente em host confiável. Os exemplos de serviço concedem acesso root ao worker.
- Estado e auditoria também são sensíveis: contêm endereços, assuntos e identidades de mensagens.
- Nunca publique snapshot, WAL, arquivos de e-mail, regras reais, logs, CSV ou backups para relatar um problema.

Relate problemas diretamente ao mantenedor por um canal privado. Não inclua credenciais ou conteúdo de mensagens em issues.
