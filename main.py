import os
import time
import redis
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

load_dotenv()

host = os.getenv("HOST")
port = os.getenv("PORT")
r = redis.Redis(host=host, port=port, decode_responses=True)

def load_existing_texts(db) -> set:
    """
    Retorna todos os precedentes cadastrados
    """
    precedents = set()

    # Busca todas as chaves que começam com 'precedente:' (exceto o contador)
    precedent_keys = db.keys('precedente:*')

    # Remove a chave do contador se ela estiver na lista
    precedent_keys = [key for key in precedent_keys if key != 'precedente:count']

    for key in precedent_keys:
        # Extrai o ID do precedente da chave
        # Busca os dados do precedente
        data = db.hgetall(key)
        precedents.add(data.get('texto_principal'))

    return precedents

def save_precedent(db, user_data):
    """
    Cria um novo precedente com ID auto-incrementado usando INCR
    """

    new_id = db.incr('precedente:count') # ID auto-incrementado
    user_key = f'precedente:{new_id}'

    db.hset(user_key, mapping=user_data)

    print(f"[REDIS] Precedente criado com ID: {new_id}")
    return new_id


def dump_card_info(card_idx: int, court: str, status: str, html_snippet: str = ""):
    """Exibe informações de diagnóstico de um card com falha."""
    print(f"  [DIAGNÓSTICO Card {card_idx}]")
    print(f"    tribunal : {court!r}")
    print(f"    situação : {status!r}")
    if html_snippet:
        print(f"    html     : {html_snippet[:300]}")


def copy_main_text(page, card, card_idx: int = 0, court: str = "", status: str = "") -> str:
    """
    Clica no botão 'Copiar texto principal' do card e lê o clipboard.
    Tenta até 5 vezes com espera crescente para garantir que o clipboard seja preenchido.
    Em caso de falha exibe diagnóstico com tribunal e situação do card.
    Retorna a string copiada ou '' em caso de falha definitiva.
    """
    attempts = 5
    delays = [0.5, 1.0, 1.5, 2.0, 3.0]  # espera após cada tentativa

    try:
        btn = card.locator("button[aria-label='Copiar texto principal para área de transferência']")
        btn.wait_for(state="visible", timeout=8000)
    except Exception as e:
        print(f"  [AVISO] Botão de cópia não encontrado: {e}")
        dump_card_info(card_idx, court, status, card.inner_html())
        return ""

    for attempt in range(1, attempts + 1):
        try:
            btn.click()
            time.sleep(delays[attempt - 1])
            text = page.evaluate("() => navigator.clipboard.readText()")
            if text and text.strip():
                return text.strip()
            if attempt < attempts:
                print(f"  [AVISO] Clipboard vazio na tentativa {attempt}, repetindo...")
        except Exception as e:
            if attempt < attempts:
                print(f"  [AVISO] Tentativa {attempt} falhou ({e}), repetindo...")
                time.sleep(delays[attempt - 1])
            else:
                print(f"  [AVISO] Todas as tentativas falharam: {e}")

    # Todas as tentativas esgotadas — mostra diagnóstico
    print(f"  [AVISO] Texto vazio após {attempts} tentativas.")
    dump_card_info(card_idx, court, status, card.inner_html())
    return ""


def extract_cards(page, existing_texts: set) -> int:
    """
    Extrai todos os cards visíveis na página atual.
    Retorna o número de itens novos salvos.
    """
    new_items = 0

    # Aguarda pelo menos 1 card aparecer (até 30s)
    try:
        page.locator("div.card.card-body").first.wait_for(state="visible", timeout=30000)
    except Exception:
        print("  [AVISO] Nenhum card ficou visível após aguardar — página pode estar vazia.")
        # Debug: imprime trecho do HTML para diagnóstico
        snippet = page.content()
        snippet_idx = snippet.find("card")
        print(f"  [DEBUG] HTML snippet: {snippet[max(0,snippet_idx-100):snippet_idx+200]}")
        return 0

    # Aguarda estabilizar (Angular pode re-renderizar a lista)
    time.sleep(2)

    cards = page.locator("div.card.card-body").all()
    print(f"  → {len(cards)} card(s) encontrado(s) nesta página.")

    for card_index, card in enumerate(cards, start=1):
        # 1ª informação: badge do tribunal
        try:
            court = card.locator("div.badgeTribunalResultado").inner_text(timeout=3000).strip()
        except Exception:
            court = ""

        # 2ª informação: situação (visível apenas em md+)
        try:
            status = card.locator("div.d-none.d-md-block.justify").inner_text(timeout=3000).strip()
        except Exception:
            status = ""

        # 3ª informação: texto principal via botão copiar
        main_text = copy_main_text(page, card, card_idx=card_index, court=court, status=status)

        if not main_text:
            continue  # diagnóstico já exibido dentro de copiar_texto_principal

        if main_text in existing_texts:
            print(f"  [Card {card_index}] Duplicata — já existe no Banco.")
            continue

        row = {
            "tribunal": court,
            "situacao": status,
            "texto_principal": main_text,
        }
        save_precedent(r, row)
        existing_texts.add(main_text)
        new_items += 1
        print(f"  [Card {card_index}] Salvo — tribunal: {court!r} | situação: {status[:60]!r}")

    return new_items


def go_to_next_page(page) -> bool:
    """
    Clica no link 'Next' da paginação.
    Retorna True se conseguiu avançar, False se não há próxima página ou está desabilitado.
    """
    try:
        # Seleciona diretamente o <a aria-label="Next"> dentro da paginação
        next_link = page.locator("ul.pagination a[aria-label='Next']")

        if next_link.count() == 0:
            return False

        # Verifica se o <li> pai tem classe 'disabled'
        parent_li = next_link.locator("xpath=..")
        class_names = parent_li.get_attribute("class") or ""
        if "disabled" in class_names:
            return False

        # Garante que está visível antes de clicar
        next_link.wait_for(state="visible", timeout=5000)
        next_link.click()

        # Aguarda os novos cards carregarem
        page.wait_for_load_state("networkidle", timeout=15000)
        time.sleep(1)
        return True
    except PlaywrightTimeout:
        print("  [AVISO] Timeout ao aguardar próxima página.")
        return False
    except Exception as e:
        print(f"  [AVISO] Erro ao avançar página: {e}")
        return False


def main():
    existing_texts = load_existing_texts(r)
    print(f"[INFO] {len(existing_texts)} texto(s) já presentes no Banco.")

    total_new = 0

    with sync_playwright() as p:
        # Precisamos de headful OU grant clipboard permissions
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()

        print("[INFO] Acessando https://pangeabnp.pdpj.jus.br/ ...")
        page.goto("https://pangeabnp.pdpj.jus.br/", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)

        # Fechar modal de release notes que bloqueia os cliques
        print("[INFO] Verificando modal de release notes...")
        try:
            modal = page.locator("release-modal")
            if modal.count() > 0:
                closed = False
                # Tenta botões de fechar comuns dentro do modal
                for selector in [
                    "release-modal button[aria-label='Close']",
                    "release-modal button[aria-label='Fechar']",
                    "release-modal button.btn-close",
                    "release-modal button.close",
                    "release-modal button",
                ]:
                    buttons = page.locator(selector).all()
                    for btn in buttons:
                        try:
                            if btn.is_visible():
                                btn.click(force=True)
                                time.sleep(0.5)
                                closed = True
                                print(f"  Modal fechado via '{selector}'")
                                break
                        except Exception:
                            pass
                    if closed:
                        break

                if not closed:
                    page.keyboard.press("Escape")
                    time.sleep(0.5)
                    print("  Modal dispensado via Escape.")

                # Aguarda o modal sumir; se persistir, remove via JS
                try:
                    modal.wait_for(state="hidden", timeout=4000)
                except Exception:
                    page.evaluate("""() => {
                        document.querySelector('release-modal')?.remove();
                        document.querySelectorAll('.modal-backdrop').forEach(e => e.remove());
                        document.body.classList.remove('modal-open');
                        document.body.style.overflow = '';
                    }""")
                    time.sleep(0.3)
                    print("  Modal removido via JavaScript.")
        except Exception as e:
            print(f"  [AVISO] Erro ao lidar com modal: {e}")

        # Clicar no botão "Pesquisar" da landing page
        # Há dois botões com aria-label='Pesquisar'; o correto é type="btn" (não type="submit")
        print("[INFO] Clicando no botão 'Pesquisar'...")
        try:
            search_button = page.locator("button[type='btn'][aria-label='Pesquisar']")
            search_button.wait_for(state="visible", timeout=10000)
            search_button.click(force=True)
            page.wait_for_load_state("networkidle", timeout=20000)
            time.sleep(2)
            print("[INFO] Resultados carregados.")
        except Exception as e:
            print(f"[ERRO] Não foi possível clicar em 'Pesquisar': {e}")
            browser.close()
            return

        # Alterar para 100 resultados por página
        print("[INFO] Ajustando para 100 resultados por página...")
        try:
            results_select = page.locator("select[aria-label='Selecione o número de resultados por página']")
            results_select.wait_for(state="visible", timeout=10000)
            # Aguarda cards atuais desaparecerem após a troca, depois aguarda novos aparecerem
            results_select.select_option(value="100")
            # Espera a lista de cards ser re-renderizada (pode sumir brevemente)
            time.sleep(1)
            page.wait_for_load_state("networkidle", timeout=20000)
            # Aguarda explicitamente pelo menos 1 card aparecer
            page.locator("div.card.card-body").first.wait_for(state="visible", timeout=30000)
            time.sleep(1)
            count = page.locator("div.card.card-body").count()
            print(f"[INFO] Paginação ajustada para 100 por página — {count} card(s) visível(is).")
        except Exception as e:
            print(f"  [AVISO] Não foi possível alterar resultados por página: {e}")

        page_number = 1
        while True:
            print(f"\n[PÁGINA {page_number}]")
            new_items = extract_cards(page, existing_texts)
            total_new += new_items

            if not go_to_next_page(page):
                print("\n[INFO] Última página atingida ou sem botão 'Next'.")
                break
            page_number += 1

        browser.close()

    print(f"\n[CONCLUÍDO] Total de registros novos salvos: {total_new}")


if __name__ == "__main__":
    main()
