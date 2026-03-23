import os
import time
import redis
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

load_dotenv()

host = os.getenv("HOST")
port = os.getenv("PORT")
r = redis.Redis(host=host, port=port, decode_responses=True)

def carregar_textos_existentes(banco) -> set:
    """
    Retorna todos os precedentes cadastrados
    """
    precedentes = set()

    # Busca todas as chaves que começam com 'precedente:' (exceto o contador)
    chaves_precedentes = banco.keys('precedente:*')

    # Remove a chave do contador se ela estiver na lista
    chaves_precedentes = [chave for chave in chaves_precedentes if chave != 'precedente:count']

    for chave in chaves_precedentes:
        # Extrai o ID do precedente da chave
        # Busca os dados do precedente
        dados = banco.hgetall(chave)
        precedentes.add(dados.get('texto_principal'))

    return precedentes

def salvar_precedente(banco, dados_usuario):
    """
    Cria um novo precedente com ID auto-incrementado usando INCR
    """

    novo_id = banco.incr('precedente:count') # ID auto-incrementado
    chave_usuario = f'precedente:{novo_id}'

    banco.hset(chave_usuario, mapping=dados_usuario)

    print(f"[REDIS] Precedente criado com ID: {novo_id}")
    return novo_id


def dump_card_info(card_idx: int, tribunal: str, situacao: str, html_snippet: str = ""):
    """Exibe informações de diagnóstico de um card com falha."""
    print(f"  [DIAGNÓSTICO Card {card_idx}]")
    print(f"    tribunal : {tribunal!r}")
    print(f"    situação : {situacao!r}")
    if html_snippet:
        print(f"    html     : {html_snippet[:300]}")


def copiar_texto_principal(page, card, card_idx: int = 0, tribunal: str = "", situacao: str = "") -> str:
    """
    Clica no botão 'Copiar texto principal' do card e lê o clipboard.
    Tenta até 5 vezes com espera crescente para garantir que o clipboard seja preenchido.
    Em caso de falha exibe diagnóstico com tribunal e situação do card.
    Retorna a string copiada ou '' em caso de falha definitiva.
    """
    tentativas = 5
    esperas = [0.5, 1.0, 1.5, 2.0, 3.0]  # espera após cada tentativa

    try:
        btn = card.locator("button[aria-label='Copiar texto principal para área de transferência']")
        btn.wait_for(state="visible", timeout=8000)
    except Exception as e:
        print(f"  [AVISO] Botão de cópia não encontrado: {e}")
        dump_card_info(card_idx, tribunal, situacao, card.inner_html())
        return ""

    for tentativa in range(1, tentativas + 1):
        try:
            btn.click()
            time.sleep(esperas[tentativa - 1])
            texto = page.evaluate("() => navigator.clipboard.readText()")
            if texto and texto.strip():
                return texto.strip()
            if tentativa < tentativas:
                print(f"  [AVISO] Clipboard vazio na tentativa {tentativa}, repetindo...")
        except Exception as e:
            if tentativa < tentativas:
                print(f"  [AVISO] Tentativa {tentativa} falhou ({e}), repetindo...")
                time.sleep(esperas[tentativa - 1])
            else:
                print(f"  [AVISO] Todas as tentativas falharam: {e}")

    # Todas as tentativas esgotadas — mostra diagnóstico
    print(f"  [AVISO] Texto vazio após {tentativas} tentativas.")
    dump_card_info(card_idx, tribunal, situacao, card.inner_html())
    return ""


def extrair_cards(page, textos_existentes: set) -> int:
    """
    Extrai todos os cards visíveis na página atual.
    Retorna o número de itens novos salvos.
    """
    novos = 0

    # Aguarda pelo menos 1 card aparecer (até 30s)
    try:
        page.locator("div.card.card-body").first.wait_for(state="visible", timeout=30000)
    except Exception:
        print("  [AVISO] Nenhum card ficou visível após aguardar — página pode estar vazia.")
        # Debug: imprime trecho do HTML para diagnóstico
        snippet = page.content()
        idx = snippet.find("card")
        print(f"  [DEBUG] HTML snippet: {snippet[max(0,idx-100):idx+200]}")
        return 0

    # Aguarda estabilizar (Angular pode re-renderizar a lista)
    time.sleep(2)

    cards = page.locator("div.card.card-body").all()
    print(f"  → {len(cards)} card(s) encontrado(s) nesta página.")

    for idx, card in enumerate(cards, start=1):
        # 1ª informação: badge do tribunal
        try:
            tribunal = card.locator("div.badgeTribunalResultado").inner_text(timeout=3000).strip()
        except Exception:
            tribunal = ""

        # 2ª informação: situação (visível apenas em md+)
        try:
            situacao = card.locator("div.d-none.d-md-block.justify").inner_text(timeout=3000).strip()
        except Exception:
            situacao = ""

        # 3ª informação: texto principal via botão copiar
        texto_principal = copiar_texto_principal(page, card, card_idx=idx, tribunal=tribunal, situacao=situacao)

        if not texto_principal:
            continue  # diagnóstico já exibido dentro de copiar_texto_principal

        if texto_principal in textos_existentes:
            print(f"  [Card {idx}] Duplicata — já existe no Banco.")
            continue

        linha = {
            "tribunal": tribunal,
            "situacao": situacao,
            "texto_principal": texto_principal,
        }
        salvar_precedente(r, linha)
        textos_existentes.add(texto_principal)
        novos += 1
        print(f"  [Card {idx}] Salvo — tribunal: {tribunal!r} | situação: {situacao[:60]!r}")

    return novos


def ir_para_proxima_pagina(page) -> bool:
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
        classes = parent_li.get_attribute("class") or ""
        if "disabled" in classes:
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
    textos_existentes = carregar_textos_existentes(r)
    print(f"[INFO] {len(textos_existentes)} texto(s) já presentes no Banco.")

    total_novos = 0

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
                fechado = False
                # Tenta botões de fechar comuns dentro do modal
                for seletor in [
                    "release-modal button[aria-label='Close']",
                    "release-modal button[aria-label='Fechar']",
                    "release-modal button.btn-close",
                    "release-modal button.close",
                    "release-modal button",
                ]:
                    btns = page.locator(seletor).all()
                    for btn in btns:
                        try:
                            if btn.is_visible():
                                btn.click(force=True)
                                time.sleep(0.5)
                                fechado = True
                                print(f"  Modal fechado via '{seletor}'")
                                break
                        except Exception:
                            pass
                    if fechado:
                        break

                if not fechado:
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
            pesquisar_btn = page.locator("button[type='btn'][aria-label='Pesquisar']")
            pesquisar_btn.wait_for(state="visible", timeout=10000)
            pesquisar_btn.click(force=True)
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
            select = page.locator("select[aria-label='Selecione o número de resultados por página']")
            select.wait_for(state="visible", timeout=10000)
            # Aguarda cards atuais desaparecerem após a troca, depois aguarda novos aparecerem
            select.select_option(value="100")
            # Espera a lista de cards ser re-renderizada (pode sumir brevemente)
            time.sleep(1)
            page.wait_for_load_state("networkidle", timeout=20000)
            # Aguarda explicitamente pelo menos 1 card aparecer
            page.locator("div.card.card-body").first.wait_for(state="visible", timeout=30000)
            time.sleep(1)
            qtd = page.locator("div.card.card-body").count()
            print(f"[INFO] Paginação ajustada para 100 por página — {qtd} card(s) visível(is).")
        except Exception as e:
            print(f"  [AVISO] Não foi possível alterar resultados por página: {e}")

        pagina = 1
        while True:
            print(f"\n[PÁGINA {pagina}]")
            novos = extrair_cards(page, textos_existentes)
            total_novos += novos

            if not ir_para_proxima_pagina(page):
                print("\n[INFO] Última página atingida ou sem botão 'Next'.")
                break
            pagina += 1

        browser.close()

    print(f"\n[CONCLUÍDO] Total de registros novos salvos: {total_novos}")


if __name__ == "__main__":
    main()
