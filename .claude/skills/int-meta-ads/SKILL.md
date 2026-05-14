---
name: int-meta-ads
description: "Query and manage Meta Ads (Facebook/Instagram) campaigns via the Marketing API. Use when you need to list campaigns, adsets or ads, fetch performance metrics (impressions, reach, clicks, spend, CPC, CPM, CTR), create new campaigns, or update campaign status and budget. Credentials are pre-configured in .env."
metadata:
  openclaw:
    requires:
      env:
        - META_SYSTEM_USER_TOKEN
        - META_AD_ACCOUNT_ID
      bins:
        - python3
    primaryEnv: META_SYSTEM_USER_TOKEN
    files:
      - "scripts/*"
---

# Meta Ads

Interact with the Meta Ads Marketing API to manage campaigns, adsets, ads and performance metrics.

## Setup (one-time)

Credentials already configured in `.env`:
```
META_SYSTEM_USER_TOKEN=<access token>
META_AD_ACCOUNT_ID=act_724108254032481
```

## Campaigns

### List active campaigns
```bash
python3 scripts/meta_ads_client.py campaigns
```

### List by status
```bash
python3 scripts/meta_ads_client.py campaigns --status ACTIVE PAUSED
```

### List all statuses
```bash
python3 scripts/meta_ads_client.py campaigns --status ACTIVE PAUSED ARCHIVED
```

## Adsets

### List adsets for a campaign
```bash
python3 scripts/meta_ads_client.py adsets <campaign_id>
```

## Ads

### List ads for a campaign
```bash
python3 scripts/meta_ads_client.py ads --campaign-id <campaign_id>
```

### List ads for an adset
```bash
python3 scripts/meta_ads_client.py ads --adset-id <adset_id>
```

## Insights (Metrics)

### Campaign metrics (last 30 days)
```bash
python3 scripts/meta_ads_client.py insights <campaign_id> --level campaign
```

### With custom date range
```bash
python3 scripts/meta_ads_client.py insights <campaign_id> --level campaign --since 2026-04-01 --until 2026-04-30
```

### Adset-level metrics
```bash
python3 scripts/meta_ads_client.py insights <campaign_id> --level adset
```

### Ad-level metrics
```bash
python3 scripts/meta_ads_client.py insights <campaign_id> --level ad
```

### Account overview (all campaigns combined)
```bash
python3 scripts/meta_ads_client.py account-insights
python3 scripts/meta_ads_client.py account-insights --since 2026-04-01 --until 2026-04-30
```

## Create Campaign

### Criar campanha com budget diário (inicia pausada)
```bash
python3 scripts/meta_ads_client.py create-campaign \
  --name "Nome da Campanha" \
  --objective OUTCOME_ENGAGEMENT \
  --daily-budget 50.00 \
  --status PAUSED
```

### Criar campanha com budget total + data de término
```bash
python3 scripts/meta_ads_client.py create-campaign \
  --name "Nome da Campanha" \
  --objective OUTCOME_SALES \
  --lifetime-budget 500.00 \
  --end-time 2026-06-30 \
  --status PAUSED
```

### Objetivos disponíveis
| Objetivo | Quando usar |
|---|---|
| `OUTCOME_AWARENESS` | Reconhecimento de marca, alcance |
| `OUTCOME_ENGAGEMENT` | Curtidas, comentários, mensagens |
| `OUTCOME_LEADS` | Geração de leads |
| `OUTCOME_SALES` | Conversões, vendas |
| `OUTCOME_TRAFFIC` | Cliques para site/app |
| `OUTCOME_APP_PROMOTION` | Instalações de app |

> **Atenção:** campanhas criadas ficam `PAUSED` por padrão para revisão antes de ativar.

## Update Campaign

### Pausar campanha
```bash
python3 scripts/meta_ads_client.py update-campaign <campaign_id> --status PAUSED
```

### Ativar campanha
```bash
python3 scripts/meta_ads_client.py update-campaign <campaign_id> --status ACTIVE
```

### Atualizar budget diário
```bash
python3 scripts/meta_ads_client.py update-campaign <campaign_id> --daily-budget 100.00
```

### Renomear campanha
```bash
python3 scripts/meta_ads_client.py update-campaign <campaign_id> --name "Novo Nome"
```

## Output

Todos os comandos retornam JSON. Métricas de insights incluem:
- `impressions` — total de exibições
- `reach` — pessoas únicas alcançadas
- `clicks` — cliques totais
- `spend` — gasto em BRL
- `cpc` — custo por clique
- `cpm` — custo por mil impressões
- `ctr` — taxa de cliques (%)
- `actions` — conversões, mensagens, etc.

## Notes

- Budgets são passados em BRL (ex: `50.00`) e convertidos automaticamente para centavos na API
- Campanhas novas sempre criadas com `PAUSED` para revisão — ative manualmente após conferir
- Para criar adsets e anúncios dentro de uma campanha, use a API diretamente ou abra o Meta Ads Manager
