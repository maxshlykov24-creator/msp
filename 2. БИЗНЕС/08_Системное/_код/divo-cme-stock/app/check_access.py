from app.sheets import access_ok, sheets_service, sheet_map

if __name__ == "__main__":
    ok, info = access_ok()
    print("ok" if ok else "FAIL", info)
    if ok:
        print("sheets:", list(sheet_map(sheets_service()).keys()))
