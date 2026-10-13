import json
import os
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from pydantic import BaseModel


# Caminhos e configurações
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATABASE_PATH = BASE_DIR / "estoque.db"

load_dotenv(BASE_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-flash-latest"
).strip()

# Dados iniciais de demonstração.
INITIAL_PRODUCTS = [
    ("Arroz Uncle João 5kg", "Grãos", 29.90, 12, "Arroz tipo 1"),
    ("Café 3 Hearts 500g", "Café", 18.50, 4, "Café torrado e moído"),
    ("Feijão Cadique 1kg", "Grãos", 8.99, 0, "Feijão carioca"),
    ("Açúcar Cristal 1kg", "Mercearia", 4.79, 3, "Açúcar cristal"),
    ("Leite Integral 1L", "Laticínios", 5.49, 20, "Leite integral"),
    ("Óleo de Soja 900ml", "Mercearia", 7.99, 2, "Óleo de soja"),
    ("Macarrão Espaguete 500g", "Massas", 3.99, 15, "Macarrão espaguete"),
    ("Molho de Tomate 300g", "Mercearia", 2.49, 0, "Molho de tomate"),
    ("Biscoito Cream Cracker", "Biscoitos", 3.79, 10, "Biscoito salgado"),
    ("Coca-Cola 2L", "Bebidas", 10.99, 6, "Refrigerante"),
    ("Água Mineral 500ml", "Bebidas", 1.50, 40, "Água mineral"),
    ("Sabão em Pó 1kg", "Limpeza", 12.90, 5, "Sabão em pó"),
    ("Papel Higiênico 12 unidades", "Higiene", 16.99, 7, "Papel higiênico"),
]

# Informações fictícias para a demonstração.
INITIAL_STORE = (
    "Mercadinho do Chico",
    "Rua Exemplo, 123, Centro, Xique-Xique - BA",
    "(77) 99999-9999",
    "Segunda a sábado, das 7h às 19h; domingo, das 7h às 12h",
)

VALID_INTENTS = {
    "product_availability",
    "product_quantity",
    "product_price",
    "product_list",
    "out_of_stock",
    "low_stock",
    "store_information",
    "unknown",
}


# A conexão permite ler e gravar dados no SQLite.
@contextmanager
def database_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row

    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


# Cria as tabelas e cadastra os dados iniciais na primeira execução.
def initialize_database():
    with database_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                price REAL NOT NULL,
                stock INTEGER NOT NULL,
                description TEXT NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS store_info (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT NOT NULL,
                address TEXT NOT NULL,
                phone TEXT NOT NULL,
                opening_hours TEXT NOT NULL
            )
        """)

        product_count = connection.execute(
            "SELECT COUNT(*) AS total FROM products"
        ).fetchone()["total"]

        if product_count == 0:
            connection.executemany(
                """
                INSERT INTO products
                    (name, category, price, stock, description)
                VALUES (?, ?, ?, ?, ?)
                """,
                INITIAL_PRODUCTS,
            )

        connection.execute(
            """
            INSERT OR IGNORE INTO store_info
                (id, name, address, phone, opening_hours)
            VALUES (1, ?, ?, ?, ?)
            """,
            INITIAL_STORE,
        )


def get_all_products():
    with database_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, name, category, price, stock, description
            FROM products
            ORDER BY name
            """
        ).fetchall()

        return [dict(row) for row in rows]


def get_store_information():
    with database_connection() as connection:
        row = connection.execute(
            "SELECT * FROM store_info WHERE id = 1"
        ).fetchone()

        return dict(row)


# Padroniza textos para facilitar buscas com ou sem acentos.
# Ex.: "café" e "cafe" passam a ser comparados da mesma forma.
def normalize_text(text):
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )

    return " ".join(text.split())


# Procura no banco pelos produtos relacionados à pergunta.
def find_products(product_query):
    query = normalize_text(product_query)

    # Ajuda a encontrar "café" quando a IA retorna "cafés".
    if query.endswith("s") and len(query) > 3:
        query = query[:-1]

    if not query:
        return []

    matching_products = []

    for product in get_all_products():
        name = normalize_text(product["name"])
        words = [word for word in query.split() if len(word) > 2]

        if query in name or (
            words and all(word in name for word in words)
        ):
            matching_products.append(product)

    return matching_products


def format_price(price):
    return f"R$ {price:.2f}".replace(".", ",")


# Usa a IA para entender o que o cliente está perguntando.
# A IA identifica a intenção, mas não decide o preço ou o estoque.

def fallback_interpret_question(question):
    """
    Interpretação local de emergência.
    Usada somente quando os modelos Gemini falham.
    """
    normalized = normalize_text(question)

    # Identifica perguntas sobre a loja.
    store_terms = [
        "endereco",
        "horario",
        "telefone",
        "funcionamento",
        "abre",
        "fecha",
    ]

    # Identifica produtos em falta.
    out_of_stock_terms = [
        "em falta",
        "sem estoque",
        "acabou",
        "acabaram",
    ]

    # Identifica produtos com estoque baixo.
    low_stock_terms = [
        "acabando",
        "estoque baixo",
        "poucas unidades",
    ]

    # Identifica pedidos para listar os produtos.
    list_terms = [
        "quais produtos",
        "lista de produtos",
        "todos os produtos",
        "o que voces vendem",
    ]

    # Escolhe a intenção mais provável.
    if any(term in normalized for term in store_terms):
        intent = "store_information"

    elif any(term in normalized for term in out_of_stock_terms):
        intent = "out_of_stock"

    elif any(term in normalized for term in low_stock_terms):
        intent = "low_stock"

    elif any(term in normalized for term in list_terms):
        intent = "product_list"

    elif any(term in normalized for term in [
        "quanto custa",
        "preco",
        "valor",
        "quanto fica",
        "quanto ficaria",
    ]):
        intent = "product_price"

    elif any(term in normalized for term in [
        "quantos",
        "quantas",
        "quantidade",
        "estoque de",
    ]):
        intent = "product_quantity"

    elif any(term in normalized for term in [
        "tem ",
        "vende",
        "disponivel",
        "possui",
    ]):
        intent = "product_availability"

    else:
        intent = "unknown"

    # Tenta identificar qual produto foi mencionado.
    product_query = ""

    for item in get_all_products():
        words = [
            word
            for word in normalize_text(item["name"]).split()
            if len(word) > 2 and not any(char.isdigit() for char in word)
        ]

        if any(word in normalized for word in words):
            product_query = item["name"]
            break

    return {
        "intent": intent,
        "product": product_query,
    }
def interpret_question(question):
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="A chave do Gemini não foi configurada. Confira o arquivo .env.",
        )

    prompt = f"""
Você classifica perguntas em português para um chatbot de mercado.

Escolha uma destas intenções:
- product_availability: perguntar se temos um produto
- product_quantity: perguntar quantas unidades existem
- product_price: perguntar o preço
- product_list: listar os produtos cadastrados
- out_of_stock: perguntar sobre produtos em falta
- low_stock: perguntar sobre produtos acabando
- store_information: perguntar endereço, telefone ou horário
- unknown: qualquer outra pergunta

Retorne somente um JSON válido com estes campos:
{{
  "intent": "uma das intenções acima",
  "product": "nome do produto ou texto vazio"
}}

Não invente produtos nem informações.

Exemplos:
"Tem arroz?" -> product_availability, produto arroz
"Quantos cafés tem?" -> product_quantity, produto café
"Quanto custa o feijão?" -> product_price, produto feijão
"Quais produtos estão acabando?" -> low_stock
"Qual é o endereço?" -> store_information

Pergunta do cliente: {question}
"""

    # Testa primeiro o modelo configurado no arquivo .env.
    # Se falhar, tenta uma alternativa.

    models_to_try = [
        GEMINI_MODEL,
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
    ]

    # Evita testar o mesmo modelo duas vezes.
    models_to_try = list(dict.fromkeys(models_to_try))

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as error:
        print(
            f"Não foi possível criar o cliente Gemini: "
            f"{type(error).__name__}: {error}"
        )

        raise HTTPException(
            status_code=502,
            detail="Não foi possível inicializar a conexão com a IA.",
        )

    for model_name in models_to_try:
        print(f"Tentando interpretar a pergunta com: {model_name}")

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                },
            )

            result = json.loads(response.text or "{}")

            intent = str(result.get("intent", "unknown"))
            product = str(result.get("product", "")).strip()

            if intent not in VALID_INTENTS:
                intent = "unknown"

            print(f"Pergunta interpretada com sucesso: {model_name}")

            return {
                "intent": intent,
                "product": product,
            }

        except Exception as error:
            print(
                f"Falha no modelo {model_name}: "
                f"{type(error).__name__}: {error}"
            )

        print(
        "Os modelos Gemini falharam. "
        "Usando a interpretação local de emergência."
    )

    return fallback_interpret_question(question)


# Monta a resposta usando informações verificadas no banco.
def answer_question(intent, product_query):
    products = get_all_products()

    if intent == "store_information":
        store = get_store_information()

        return (
            f"Bem-vindo ao {store['name']}!\n"
            f"Endereço: {store['address']}\n"
            f"Telefone: {store['phone']}\n"
            f"Horário: {store['opening_hours']}"
        )

    if intent == "product_list":
        if not products:
            return "Não há produtos cadastrados no momento."

        lines = ["Estes são os produtos cadastrados:"]

        for product in products:
            if product["stock"] > 0:
                stock_status = (
                    f"{product['stock']} unidade(s) em estoque"
                )
            else:
                stock_status = "sem estoque no momento"

            lines.append(
                f"- {product['name']} — "
                f"{format_price(product['price'])}; "
                f"{stock_status}."
            )

        return "\n".join(lines)

    if intent == "out_of_stock":
        missing = [p for p in products if p["stock"] == 0]

        if not missing:
            return "No momento, nenhum produto está sem estoque."

        names = ", ".join(p["name"] for p in missing)
        return f"Estes produtos estão em falta: {names}."

    if intent == "low_stock":
        low = [
            p for p in products
            if 0 < p["stock"] <= 5
        ]

        if not low:
            return "No momento, não há produtos com estoque baixo."

        lines = [
            f"- {p['name']}: {p['stock']} unidade(s)"
            for p in low
        ]

        return "Estes produtos estão com estoque baixo:\n" + "\n".join(lines)

    if intent in {
        "product_availability",
        "product_quantity",
        "product_price",
    }:
        matches = find_products(product_query)

        if not matches:
            requested = product_query or "esse produto"

            return (
                f"Não encontrei '{requested}' no cadastro da vendinha. "
                "Confira o nome ou pergunte sobre outro produto."
            )

        answers = []

        for product in matches:
            name = product["name"]
            stock = product["stock"]
            price = format_price(product["price"])

            if intent == "product_availability":
                if stock > 0:
                    answer = (
                        f"Sim! Temos {name}. "
                        f"Há {stock} unidade(s) no estoque."
                    )
                else:
                    answer = f"No momento, estamos sem {name}."

            elif intent == "product_quantity":
                answer = (
                    f"Temos {stock} unidade(s) de {name} no estoque."
                )

            else:
                answer = f"O preço de {name} é {price}."

            answers.append(answer)

        return "\n".join(answers)

    return (
        "Posso ajudar com disponibilidade, quantidade e preço dos produtos, "
        "itens em falta ou acabando, além do endereço e horário da loja."
    )


# Criação da aplicação web.
initialize_database()

app = FastAPI(title="Chatbot da Vendinha")

app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR),
    name="static",
)


class ChatRequest(BaseModel):
    message: str


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/chat")
def chat(request: ChatRequest):
    message = request.message.strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Digite uma pergunta antes de enviar.",
        )

    interpretation = interpret_question(message)

    response = answer_question(
        interpretation["intent"],
        interpretation["product"],
    )

    return {"response": response}


@app.get("/api/products")
def list_products():
    return get_all_products()


@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "database_exists": DATABASE_PATH.exists(),
        "gemini_configured": bool(GEMINI_API_KEY),
    }