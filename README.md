# Radar de preços

Robô que guarda, duas vezes por dia (9h e 21h de Brasília), o preço de consoles, jogos e acessórios para jogar GTA 6.

- `config/produtos.json`: a lista de produtos e, por loja, o código ou a busca.
- `coletor/coletar.py`: o robô. Só usa Python padrão.
- `data/precos.csv`: o histórico. Uma linha por produto, loja e coleta.
- `data/ultimo.json`: a coleta mais recente, para o site ler.
- `.github/workflows/coleta.yml`: o agendamento no GitHub Actions (gratuito em repositório público).

Status possíveis por linha: `ok`, `esgotado`, `sem_preco_na_pagina`, `nenhum_resultado_valido`, `erro_http_<código>`.

A primeira linha de cada produto da KaBuM (10/11/2026, 23h50) foi registrada à mão, antes do robô ligar.

Para rodar na hora: aba **Actions** → **Coleta de preços** → **Run workflow**.
