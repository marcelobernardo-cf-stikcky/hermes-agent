"""Cache do browser fora do backup; sessao dentro.

RED contra o codigo anterior: sem _is_browser_cache, todo cache entrava no zip.
"""
import sys
from pathlib import Path

from hermes_cli.backup import _is_browser_cache, _iter_backup_files, _should_exclude


def test_cache_dirs_do_browser_ficam_de_fora():
    for rel in ("chrome-debug/Default/Cache/data_0",
                "chrome-debug/Default/Code Cache/js/index",
                "chrome-debug/Default/Service Worker/CacheStorage/x",
                "chrome-debug/Default/GPUCache/data_1",
                "chrome-debug/optimization_guide_model_store/1/model.tflite",
                "chrome-debug/Safe Browsing/db",
                "chrome-debug/component_crx_cache/abc"):
        assert _should_exclude(Path(rel)), rel


def test_sessao_e_preferencias_sobrevivem():
    # o ponto do meio-termo: sem estes, restaurar exige refazer todo login
    for rel in ("chrome-debug/Default/Network/Cookies",
                "chrome-debug/Default/Preferences",
                "chrome-debug/Default/Login Data",
                "chrome-debug/Default/Web Data",
                "chrome-debug/Local State",
                "chrome-debug/First Run"):
        assert not _should_exclude(Path(rel)), rel


def test_nao_pega_cache_fora_do_perfil_do_browser():
    # 'Cache' de uma skill e dado do usuario, nao cache do Chrome
    for rel in ("skills/minha/Cache/arquivo.md",
                "profiles/whisper/Code Cache/nota.txt",
                "Cache/solto.txt"):
        assert not _is_browser_cache(Path(rel)), rel


def test_walk_nao_desce_na_arvore_de_cache(tmp_path):
    # poda no os.walk: sem ela o backup varre 1 GB para descartar arquivo a arquivo
    raiz = tmp_path
    cache = raiz / "chrome-debug" / "Default" / "Cache"
    cache.mkdir(parents=True)
    (cache / "data_0").write_text("x" * 100)
    net = raiz / "chrome-debug" / "Default" / "Network"
    net.mkdir(parents=True)
    (net / "Cookies").write_text("sessao")

    rels = {str(r).replace("\\", "/") for _, r in
            _iter_backup_files(raiz, raiz / "saida.zip")}
    assert "chrome-debug/Default/Network/Cookies" in rels
    assert not any("Cache" in r for r in rels), rels


if __name__ == "__main__":
    import tempfile
    test_cache_dirs_do_browser_ficam_de_fora()
    test_sessao_e_preferencias_sobrevivem()
    test_nao_pega_cache_fora_do_perfil_do_browser()
    with tempfile.TemporaryDirectory() as d:
        test_walk_nao_desce_na_arvore_de_cache(Path(d))
    print("SELFCHECK=PASS")
