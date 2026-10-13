# Balcão do Chico

Chatbot de atendimento para um mercadinho, desenvolvido com Python, FastAPI, SQLite e Google Gemini.

O projeto permite consultar produtos, preços, quantidades em estoque, produtos em falta, além do endereço e horário de funcionamento da loja.

## Tecnologias

- Python
- FastAPI
- SQLite
- Google Gemini API
- HTML, CSS e JavaScript

## Como executar

### 1. Pré-requisitos

- Python instalado
- Uma chave de API do Google Gemini

### 2. Instale as dependências

Abra o terminal na pasta do projeto e execute:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 3. Configure a API

Copie o arquivo `.env.example`, renomeie a cópia para `.env` e configure sua chave:

```dotenv
GEMINI_API_KEY=sua_chave_aqui
GEMINI_MODEL=seu_modelo_compatível
```

Utilize um modelo disponível e compatível com sua chave da API.

### 4. Inicie a aplicação

```powershell
python -m uvicorn main:app --reload
```

Abra http://127.0.0.1:8000 no navegador.

## Endpoints

- `POST /api/chat`: envia perguntas ao chatbot.
- `GET /api/products`: consulta os produtos cadastrados.
- `GET /api/health`: verifica o estado básico da aplicação.

**Observação:** o projeto utiliza dados iniciais de demonstração. As informações da loja e o estoque devem ser ajustados conforme necessário.