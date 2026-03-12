# PrecedentIA - Scraper

Serviço de **web scraping** do sistema **PrecedentIA**, responsável por coletar e armazenar automaticamente dados de precedentes jurídicos a partir de fontes públicas, disponibilizando-os via cache para consumo por outros serviços da plataforma.

## 🚀 Tecnologias Utilizadas

O projeto utiliza as seguintes tecnologias e bibliotecas:

- **Playwright** - Automação de navegadores para raspagem de páginas dinâmicas
- **Redis** - Armazenamento em cache dos dados coletados, disponibilizados para consumo por outros serviços
- **Python-dotenv** - Gerenciamento de variáveis de ambiente via arquivo `.env`

## ⚙️ Rodando o Projeto

### 1️⃣ Crie e ative o ambiente virtual

```bash
python -m venv .venv       # ou python3 no Linux
source .venv/bin/activate  # Linux/macOS
.venv\Scripts\activate     # Windows
```

### 2️⃣ Instale as dependências

```bash
pip install -r requirements.txt
```

### 3️⃣ Instale os navegadores do Playwright

```bash
playwright install
```

### 4️⃣ Configure as variáveis de ambiente

Copie o arquivo de exemplo e preencha com os valores adequados:

```bash
cp .env.example .env
```

> Certifique-se de configurar corretamente a URL de conexão com o Redis (`REDIS_URL`).

### 5️⃣ Execute o scraper

```bash
python app/main.py
```

## Saiba mais

Para verificar as padronizações usadas neste projeto, bem como demais documentações, visite o nosso [repositório principal](https://github.com/FR0M-ZER0/PrecedentIA)