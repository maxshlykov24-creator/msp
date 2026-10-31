#!/usr/bin/env python3
"""Check content, page geometry and embedded fonts; render the final PDF for visual QA."""
import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path


def verify(args):
    try:
        from pypdf import PdfReader
        from PIL import Image, ImageDraw
    except ImportError as error:
        raise RuntimeError('Запусти Python из load_workspace_dependencies: нужны pypdf и Pillow') from error
    deck = json.loads(Path(args.deck).read_text())
    reader = PdfReader(args.pdf)
    if len(reader.pages) != len(deck['slides']):
        raise ValueError('Число страниц PDF отличается от числа слайдов')
    normalize = lambda s: re.sub(r'\s+', '', s)
    fonts = {}
    for index, (page, slide) in enumerate(zip(reader.pages, deck['slides']), 1):
        if abs(float(page.mediabox.width) / float(page.mediabox.height) - 16 / 9) > .001:
            raise ValueError(f'Страница {index}: неверный формат, ожидается 16:9')
        extracted = normalize(page.extract_text())
        for key in (['text', 'reference'] if slide['kind'] == 'quote' else []):
            if normalize(slide[key]) not in extracted:
                raise ValueError(f'Страница {index}: не найден полный {key}')
        for resource in page['/Resources'].get('/Font', {}).values():
            font = resource.get_object()
            if font.get('/Subtype') == '/Type3':
                # Type3 stores its glyph outlines in the PDF CharProcs instead of a FontFile.
                if not font.get('/CharProcs'):
                    raise ValueError(f'Страница {index}: пустой Type3 шрифт')
                continue
            descendant = font.get('/DescendantFonts', [font])[0].get_object()
            descriptor = descendant.get('/FontDescriptor')
            descriptor = descriptor.get_object() if descriptor else {}
            embedded = any(key in descriptor for key in ('/FontFile', '/FontFile2', '/FontFile3'))
            fonts[str(font.get('/BaseFont'))] = embedded
    if not fonts or not all(fonts.values()):
        raise ValueError(f'Есть невстроенные шрифты: {fonts}')
    qa = Path(args.qa_dir).resolve()
    qa.mkdir(parents=True, exist_ok=True)
    poppler = args.pdftoppm or shutil.which('pdftoppm')
    if not poppler:
        raise RuntimeError('Найди pdftoppm через load_workspace_dependencies и передай --pdftoppm ПУТЬ')
    subprocess.run([poppler, '-scale-to', '800', '-png', str(Path(args.pdf).resolve()), str(qa / 'pdf')], check=True)
    for batch in range((len(reader.pages) + 5) // 6):
        sheet = Image.new('RGB', (1600, 1440), '#dddddd')
        draw = ImageDraw.Draw(sheet)
        for slot, page_no in enumerate(range(batch * 6 + 1, min(batch * 6 + 7, len(reader.pages) + 1))):
            pngs = [p for p in qa.glob('pdf-*.png') if int(p.stem.split('-')[-1]) == page_no]
            if len(pngs) != 1:
                raise ValueError(f'Нет однозначного рендера PDF страницы {page_no}')
            with Image.open(pngs[0]) as im:
                sheet.paste(im.convert('RGB'), ((slot % 2) * 800, (slot // 2) * 480))
            draw.text(((slot % 2) * 800 + 8, (slot // 2) * 480 + 454), f'{page_no:02}', fill='black')
        sheet.save(qa / f'contact-{batch + 1:02}.jpg')
    report = {'pages': len(reader.pages), 'ratio': '16:9', 'complete_quotes': True,
              'embedded_fonts': fonts, 'rendered_pdf_pages': len(reader.pages),
              'visual_review': 'pending: inspect every contact sheet; automated checks do not prove design quality'}
    (qa / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--deck', required=True)
    p.add_argument('--pdf', required=True)
    p.add_argument('--qa-dir', required=True)
    p.add_argument('--pdftoppm')
    args = p.parse_args()
    try:
        verify(args)
    except (ValueError, OSError, RuntimeError) as error:
        p.exit(1, f'Ошибка проверки PDF: {error}\n')
