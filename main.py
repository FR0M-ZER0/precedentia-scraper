import os
import time
import redis
from dotenv import load_dotenv
import requests

REQUEST_TIMEOUT_SECONDS = 30

load_dotenv()

host = os.getenv('HOST')
port = os.getenv('PORT')

r = redis.Redis(host=host, port=port, decode_responses=True)

def load_existing_texts(db) -> set:
    """
    Retorna todos os precedentes cadastrados
    """
    precedents = set()

    # scan_iter evita varredura bloqueante do Redis em bases maiores
    for key in db.scan_iter(match='precedent:*'):
        if key == 'precedent:count':
            continue

        # Extrai o ID do precedente da chave
        # Busca os dados do precedente
        data = db.hgetall(key)
        dedupe_text = data.get('url')  # Usar a URL para deduplicação
        if dedupe_text:
            precedents.add(dedupe_text)

    return precedents


def map_species(tipo: str) -> str:
    tipos = {
        'SUM': 'Súmula',
        'SV': 'Súmula Vinculante',
        'RG': 'Repercussão Geral',
        'ADI': 'Ação Direta de Inconstitucionalidade',
        'ADC': 'Ação Declaratória de Constitucionalidade',
        'ADO': 'Ação Direta de Inconstitucionalidade por Omissão',
        'ADPF': 'Arguição de Descumprimento de Preceito Fundamental',
        'IAC': 'Incidente de Assunção de Competência',
        'SIRDR': 'Súmula do IRDR',
        'RR': 'Recurso Repetitivo',
        'CT': 'Consulta',
        'IRDR': 'Incidente de Resolução de Demandas Repetitivas (IRDR)',
        'IRR': 'Incidente de Recursos Repetitivos',
        'PUIL': 'Pedido de Uniformização de Interpretação de Lei',
        'NT': 'Nota Técnica',
        'OJ': 'Orientação Jurisprudencial',
    }
    return tipos.get(tipo, tipo)


def save_precedent(db, user_data):
    """
    Cria um novo precedente com ID auto-incrementado usando INCR
    """

    new_id = db.incr('precedent:count')  # ID auto-incrementado
    user_key = f'precedent:{new_id}'

    db.hset(user_key, mapping=user_data)

    return new_id


def normalize_text(text):
    try:
        normalized = text
        replacements = [
            ('\n', ' '),
            ('\r', ' '),
            ('\t', ' '),
            ('<br>', ' '),
            ('<br/>', ' '),
            ('</br>', ' '),
            ('<p>', ' '),
            ('</p>', ' '),
            ('<i/>', ' '),
            ('</i>', ' '),
            ('<b/>', ' '),
            ('</b>', ' '),
        ]
        for old, new in replacements:
            normalized = normalized.replace(old, new)

        # Colapsa espaços duplicados gerados pelas substituições
        while '  ' in normalized:
            normalized = normalized.replace('  ', ' ')

        return normalized.strip()
    except AttributeError:
        return ""


def main():

    existing_texts = load_existing_texts(r)

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    })

    url = "https://pangeabnp.pdpj.jus.br/api/v1/precedentes"

    pagina = 1

    while True:
        try:

            filtro = {"filtro": {
                "buscaGeral": "",
                "todasPalavras": "",
                "quaisquerPalavras": "",
                "semPalavras": "",
                "trechoExato": "",
                "atualizacaoDesde": "",
                "atualizacaoAte": "",
                "cancelados": False,
                "ordenacao": "Text",
                "nr": "",
                "pagina": pagina,
                "tamanhoPagina": 100,
                "orgaos": ["STF", "STJ", "TST", "STM", "TNU", "TRF01", "TRF02", "TRF03", "TRF04", "TRF05", "TRF06", "TJAC", "TJAL", "TJAP", "TJAM", "TJBA", "TJCE", "TJDF", "TJES", "TJGO", "TJMA", "TJMT", "TJMS", "TJMG", "TJPA", "TJPB", "TJPR", "TJPE", "TJPI", "TJRJ", "TJRN", "TJRS", "TJRO", "TJRR", "TJSC", "TJSP", "TJSE", "TJTO", "TRT01", "TRT02", "TRT03", "TRT04", "TRT05", "TRT06", "TRT07", "TRT08", "TRT09", "TRT10", "TRT11", "TRT12", "TRT13", "TRT14", "TRT15", "TRT16", "TRT17", "TRT18", "TRT19", "TRT20", "TRT21", "TRT22", "TRT23", "TRT24"],
                "tipos": ["SUM", "SV", "RG", "ADI", "ADC", "ADO", "ADPF", "IAC", "SIRDR", "RR", "CT", "IRDR", "IRR", "PUIL", "NT", "OJ"]
            }}

            response = session.post(url, json=filtro, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()

            for item in data.get('resultados', []):
                precedent_type = normalize_text(item.get('tipo', ''))
                precedent_number = item.get('nr', '')

                row = {
                    "tribunal": normalize_text(item.get('orgao')),
                    "situation": normalize_text(item.get('situacao')),
                    "species": map_species(normalize_text(item.get('tipo', ''))),
                    "name": f"{map_species(precedent_type)} nº {precedent_number}" if precedent_number else map_species(precedent_type),
                    "question": normalize_text(item.get('questao')),
                    "description": normalize_text(item.get('tese')),
                    "summary": "",
                    "url": f"https://pangeabnp.pdpj.jus.br/pesquisa?orgao={normalize_text(item.get('orgao'))}&tipo={normalize_text(item.get('tipo'))}&nr={item.get('nr')}",
                    "last_update": normalize_text(item.get('ultimaAtualizacao')),
                }

                if row['url'] in existing_texts:
                    print(
                        f"[INFO] Precedente já existe, pulando: {row['url']}")
                    continue

                save_precedent(r, row)
                existing_texts.add(row['url'])

                print(f"[INFO] Salvo precedente: {row.get('url')}")

            pagina += 1
            print(f"[INFO] Avançando para a página {pagina}...")
            # Aguardar 5 segundos antes de fazer a próxima requisição
            time.sleep(5)

        except (requests.RequestException, ValueError, redis.RedisError) as e:
            print(f"[ERROR] Erro na requisição: {e}")
            break

if __name__ == "__main__":
    main()