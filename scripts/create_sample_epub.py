"""
Génère un livre EPUB japonais de démonstration dans le dossier Calibre
pour tester immédiatement :
- Le nettoyage des balises <ruby>/<rt> et le mélange des polices (<span class="gothic">, <b>)
- La normalisation des espacements (alinéas '　', espaces après '！', emphase espacée 'ぜ っ た い')
- La détection précise des phrases et des verbes conjugués
- Le minage automatique vers Anki avec soulignement (<u>mot</u>) dans la phrase de contexte.
"""

import zipfile
from pathlib import Path

from app.config import UPLOADS_DIR


def create_sample_japanese_epub() -> Path:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    epub_path = UPLOADS_DIR / "La_Bibliotheque_Silencieuse.epub"

    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

    content_opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>静かな図書館の物語 (Récit de la Bibliothèque Silencieuse)</dc:title>
    <dc:creator>夏目 漱石・テスト文庫</dc:creator>
    <dc:language>ja</dc:language>
    <dc:identifier id="BookId">urn:uuid:yomicalibre-sample-001</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="chap2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chap1"/>
    <itemref idref="chap2"/>
  </spine>
</package>"""

    toc_ncx = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:uuid:yomicalibre-sample-001"/></head>
  <docTitle><text>静かな図書館の物語</text></docTitle>
  <navMap>
    <navPoint id="navPoint-1" playOrder="1">
      <navLabel><text>第一章：古い扉と秘密の本</text></navLabel>
      <content src="chapter1.xhtml"/>
    </navPoint>
    <navPoint id="navPoint-2" playOrder="2">
      <navLabel><text>第二章：夜の窓と記憶の言葉</text></navLabel>
      <content src="chapter2.xhtml"/>
    </navPoint>
  </navMap>
</ncx>"""

    chapter1_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja">
<head>
  <title>第一章：古い扉と秘密の本</title>
</head>
<body>
  <h1>第一章：古い扉と秘密の本</h1>
  <p>　彼は静かに古い<ruby>図書館<rt>としょかん</rt></ruby>の<span class="font-gothic">重い扉</span>を開けて、深い影の中へと足を踏み入れた。</p>
  <p>　窓から優しい<ruby>光<rt>ひかり</rt></ruby>が落ちて、机の上には一冊の新しい<b>物語</b>の本が置かれていた。なぜこんな場所に誰にも知られていない本があるのだろうか？　彼はゆっくりと歩きながらその理由を考えた。</p>
  <p>　「まだ誰も知らない<span style="font-family: serif; font-weight: bold;">秘密</span>がこの本には書かれているんだ！」と彼女は強い声で言った。しかし彼はすぐにはその言葉を信じなかった。</p>
  <p>　「ぜ っ た い に<ruby>忘<rt>わす</rt></ruby>れないで！」　彼女の声は静かな部屋の中にいつまでも続いていた。</p>
</body>
</html>"""

    chapter2_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja">
<head>
  <title>第二章：夜の窓と記憶の言葉</title>
</head>
<body>
  <h1>第二章：夜の窓と記憶の言葉</h1>
  <p>　夜の雨が静かに街の道を濡らしていた。私は温かいコーヒーを飲みながら、昨日見つけた不思議な本のページをゆっくりと開いた。</p>
  <p>　そこには美しい文字で人間の心と記憶の<ruby>意味<rt>いみ</rt></ruby>が書かれていた。「本当の言葉を探すために、私たちはいつも新しい世界を選んでいる」と書いてあった。</p>
</body>
</html>"""

    with zipfile.ZipFile(str(epub_path), "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/toc.ncx", toc_ncx)
        zf.writestr("OEBPS/chapter1.xhtml", chapter1_xhtml)
        zf.writestr("OEBPS/chapter2.xhtml", chapter2_xhtml)

    return epub_path


if __name__ == "__main__":
    p = create_sample_japanese_epub()
    print(f"EPUB d'exemple créé : {p}")
