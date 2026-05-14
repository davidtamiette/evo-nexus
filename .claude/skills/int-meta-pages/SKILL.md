---
name: int-meta-pages
description: "Publish, schedule and manage organic posts on Facebook Pages via the Graph API. Use when you need to post text, images or links to a Facebook Page, schedule future posts, list recent/scheduled posts, or delete posts. Credentials are pre-configured in .env (reuses META_SYSTEM_USER_TOKEN)."
metadata:
  openclaw:
    requires:
      env:
        - META_SYSTEM_USER_TOKEN
      bins:
        - python3
    primaryEnv: META_SYSTEM_USER_TOKEN
    files:
      - "scripts/*"
---

# Meta Pages — Organic Publishing

Manage organic content on Facebook Pages via the Graph API v21.0.
Uses the same `META_SYSTEM_USER_TOKEN` as `int-meta-ads` — page tokens are fetched dynamically.

## Pages com acesso configurado

| Página | ID | Pode postar |
|--------|-----|------------|
| Ateliê Mirian Azevedo | 1058585030667680 | ✓ |
| Cognitiva AI | 587745454431764 | ✓ |
| Malvada Gula em Portugal | 732106223577257 | ✓ |
| Curso de Atendimento Pré Hospitalar Online | 408882436523239 | ✓ |
| Ápia Ambiental Engenharia e Consultoria | 504324323387103 | ✓ |
| Wizard BH Camargos | 834278116684800 | ✓ |
| Arte Feminina | 1409365215973092 | ✓ |
| Gata do Cruzeiro | 312452445555857 | ✓ |
| Madgamers | 168169700015078 | ✓ |
| Gata do Atlético | 494854083919053 | somente análise |

## API Client

```bash
python3 {project-root}/.claude/skills/int-meta-pages/scripts/meta_pages_client.py <command> [options]
```

O parâmetro `--page` aceita nome parcial da página (case-insensitive) ou o ID numérico.

## Listar páginas

```bash
python3 scripts/meta_pages_client.py list-pages
```

## Publicar agora (texto)

```bash
python3 scripts/meta_pages_client.py post \
  --page "Ateliê" \
  --message "Novos doces disponíveis! 🍰"

# Com link
python3 scripts/meta_pages_client.py post \
  --page "Ateliê" \
  --message "Confira nosso site!" \
  --link https://exemplo.com.br
```

## Agendar post (texto)

Horário em BRT (UTC-3). Mínimo +10 minutos, máximo +6 meses.

```bash
python3 scripts/meta_pages_client.py schedule \
  --page "Ateliê" \
  --message "Feliz Dia das Mães! 🌸" \
  --time "2026-05-11T10:00:00"
```

## Publicar foto agora

A imagem precisa ser uma URL pública acessível.

```bash
python3 scripts/meta_pages_client.py post-image \
  --page "Ateliê" \
  --image-url "https://exemplo.com/foto.jpg" \
  --message "Legenda da foto"
```

## Agendar foto

```bash
python3 scripts/meta_pages_client.py schedule-image \
  --page "Ateliê" \
  --image-url "https://exemplo.com/foto.jpg" \
  --message "Legenda" \
  --time "2026-05-15T18:00:00"
```

## Listar posts publicados

```bash
python3 scripts/meta_pages_client.py list-posts --page "Ateliê" --limit 10
```

## Listar posts agendados

```bash
python3 scripts/meta_pages_client.py list-scheduled --page "Ateliê"
```

## Deletar post

```bash
python3 scripts/meta_pages_client.py delete-post \
  --page "Ateliê" \
  --post-id 1058585030667680_123456789
```

## Observações

- Todos os horários de agendamento são em **BRT (UTC-3)**
- Imagens para post com foto devem ser URLs públicas (HTTPS)
- Para Instagram, usar a skill `int-instagram` (publicação via Content Publishing API — separada)
- Token expira se o System User Token for revogado — renovar via Meta Business Manager
