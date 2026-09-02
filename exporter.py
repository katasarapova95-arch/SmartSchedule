from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape

PALETTE={
"Русский язык":"F7B2D8","Литература":"D8C2F0","Математика":"A5D7F2","Алгебра":"88CAE8",
"Геометрия":"89D4D0","Английский язык":"B9E6A5","История":"F6D59B","Обществознание":"F5B8A6",
"География":"D6E48B","Биология":"99E3C4","Физика":"8EC5E8","Химия":"F0C0B9",
"Информатика":"CAB8F1","Физическая культура":"F5D978"}

def room_short_export(value):
    text=str(value or "").strip()
    return {"Спортивный зал":"с/з","спортивный зал":"с/з","Спортзал":"с/з","спортзал":"с/з"}.get(text,text)

def _col(n):
    s=""
    while n:
        n,rem=divmod(n-1,26);s=chr(65+rem)+s
    return s

def _cell(ref,val,style):
    if val is None: return ""
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t>{escape(str(val))}</t></is></c>'

def export_xlsx(data,shift,result,path,only_day=None):
    days=[only_day] if only_day else data["school"]["days"]
    classes=result.get("classes",[])
    rows=[]
    rows.append([data["school"]["name"],data["school"]["year"],f"Смена {shift}"])
    rows.append([])
    for day in days:
        rows.append([day])
        header=["Время"]
        for c in classes: header += [c,"Каб."]
        rows.append(header)
        for i,t in enumerate(result.get("times",[])):
            row=[f"{i+1}. {t}"]
            for c in classes:
                v=result.get("grid",{}).get(day,{}).get(str(i),{}).get(c,{})
                row += ([v.get("subject",""),room_short_export(v.get("room",""))] if v else ["",""])
            rows.append(row)
        rows.append([])

    maxcols=max([len(r) for r in rows]+[1])
    widths=[16]
    for _ in classes: widths += [22,7]
    while len(widths)<maxcols: widths.append(16)

    sheet_rows=[]
    for ridx,row in enumerate(rows,1):
        cells=[]
        for cidx,val in enumerate(row,1):
            if val=="": continue
            if ridx==1:
                style=1
            elif len(row)==1:
                style=2
            elif val=="Каб." or val=="Время" or val in classes:
                style=3
            elif cidx % 2 == 0 and val in PALETTE:
                style=4+list(PALETTE).index(val)
            else:
                style=0
            cells.append(_cell(f"{_col(cidx)}{ridx}",val,style))
        sheet_rows.append(f'<row r="{ridx}">'+"".join(cells)+"</row>")

    cols="".join(f'<col min="{i+1}" max="{i+1}" width="{widths[i]}" customWidth="1"/>' for i in range(maxcols))
    sheet_xml=(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<cols>{cols}</cols><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    fills=['FFFFFF','EAE7FF','F4F3F8']+list(PALETTE.values())
    fills_xml='<fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'+''.join(f'<fill><patternFill patternType="solid"><fgColor rgb="{x}"/><bgColor indexed="64"/></patternFill></fill>' for x in fills)
    fonts='<font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="14"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font>'
    xfs=[
      '<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>',
      '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>',
      '<xf numFmtId="0" fontId="0" fillId="3" borderId="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>',
      '<xf numFmtId="0" fontId="2" fillId="3" borderId="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>']
    for idx,_ in enumerate(PALETTE):
        fill_id=4+idx
        xfs.append(f'<xf numFmtId="0" fontId="2" fillId="{fill_id}" borderId="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>')
    styles_xml=('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
      '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
      f'<fonts count="3">{fonts}</fonts><fills count="{len(fills)+2}">{fills_xml}</fills>'
      '<borders count="2"><border/><border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/></border></borders>'
      f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs></styleSheet>')
    workbook=('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
              'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
              '<sheets><sheet name="Расписание" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels=('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
          '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
          '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
          '</Relationships>')
    rootrels=('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
              '</Relationships>')
    content=('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
             '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
             '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
             '</Types>')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(path,"w",ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",content)
        z.writestr("_rels/.rels",rootrels)
        z.writestr("xl/workbook.xml",workbook)
        z.writestr("xl/_rels/workbook.xml.rels",rels)
        z.writestr("xl/worksheets/sheet1.xml",sheet_xml)
        z.writestr("xl/styles.xml",styles_xml)
    return path
