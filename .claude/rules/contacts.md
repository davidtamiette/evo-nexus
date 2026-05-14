# Agenda de Contatos — Diretriz para todos os agentes

A agenda centralizada de contatos fica em `workspace/sales/contacts.md`.

## Regra obrigatória

Sempre que David informar um novo contato durante qualquer sessão — nome, telefone, papel, instância WhatsApp ou qualquer outro dado de identificação — o agente ativo deve **imediatamente** adicionar ou atualizar o contato na agenda, sem precisar ser solicitado.

## Estrutura da agenda

```markdown
| Nome | Telefone | Papel | Instância WA | Observações |
|---|---|---|---|---|
| Nome do contato | +55 11 99999-9999 | Cliente / Amigo / Parceiro / etc. | INSTANCIA | Notas relevantes |
```

## Campos

| Campo | Descrição |
|---|---|
| **Nome** | Nome completo ou como David se refere à pessoa |
| **Telefone** | Formato E.164 sem `+` para WhatsApp (ex: 5531999990000) |
| **Papel** | Relação com David (Cliente, Amigo, Parceiro, Cunhada, Fornecedor, etc.) |
| **Instância WA** | Instância Evolution API usada para enviar mensagens (ex: DAVIDTAMIETTE, COGNITIVA-AI) |
| **Observações** | Contexto relevante (preferências, projetos, etc.) |

## Quando atualizar

- Novo contato mencionado → **adicionar linha**
- Papel ou informação corrigida → **atualizar linha existente**
- Contato usado em envio de mensagem → registrar a instância utilizada se ainda não estiver preenchida

## Contatos de teste padrão

Para testes de notificação (e-mail e WhatsApp), usar sempre:
- **E-mail:** davidtamiette@gmail.com
- **WhatsApp:** 5531982302185 (instância COGNITIVA-AI)
