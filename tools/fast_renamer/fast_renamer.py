import os
import shutil
from pathlib import Path

# ==================== تنظیمات ====================
FOLDER_A = "A"          # پوشه‌ای که عکس‌های اوریجینال با اسم‌های مختلف هستن
FOLDER_B = "B"          # پوشه‌ای که فقط یک عکس آپ‌اسکیل‌شده توشه
# =================================================

def get_image_files(folder):
    """همه فایل‌های تصویری داخل پوشه رو برمی‌گردونه"""
    extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.tga', '.webp', '.dds'}
    files = []
    for f in Path(folder).iterdir():
        if f.is_file() and f.suffix.lower() in extensions:
            files.append(f)
    return sorted(files)

def main():
    print("=" * 55)
    print("Fast Texture Duplicator + Renamer")
    print("=" * 55)

    folder_a = Path(FOLDER_A)
    folder_b = Path(FOLDER_B)

    if not folder_a.exists():
        print(f"[خطا] پوشه A پیدا نشد: {FOLDER_A}")
        return
    if not folder_b.exists():
        print(f"[خطا] پوشه B پیدا نشد: {FOLDER_B}")
        return

    # فایل‌های پوشه A (اسم‌هاشون مهمه)
    files_a = get_image_files(folder_a)
    if not files_a:
        print("[خطا] هیچ فایل تصویری داخل پوشه A پیدا نشد!")
        return

    # فایل آپ‌اسکیل‌شده داخل B
    files_b = get_image_files(folder_b)
    if not files_b:
        print("[خطا] هیچ فایل تصویری داخل پوشه B پیدا نشد!")
        return
    if len(files_b) > 1:
        print(f"[هشدار] بیش از یک فایل داخل B هست. از اولی استفاده می‌کنم: {files_b[0].name}")
    
    source_upscaled = files_b[0]
    print(f"\nفایل منبع (آپ‌اسکیل‌شده): {source_upscaled.name}")
    print(f"تعداد فایل در A: {len(files_a)}")
    print("-" * 55)

    # اول فایل‌های قبلی B رو پاک می‌کنیم (به جز خود سورس)
    for f in files_b:
        if f != source_upscaled:
            f.unlink()
            print(f"حذف شد: {f.name}")

    # حالا تکثیر + تغییر اسم
    success = 0
    for i, target in enumerate(files_a, 1):
        new_name = target.name                    # دقیقاً همون اسم فایل A
        dest_path = folder_b / new_name

        try:
            shutil.copy2(source_upscaled, dest_path)
            success += 1
            if i % 20 == 0 or i == len(files_a):
                print(f"[{i}/{len(files_a)}] ساخته شد → {new_name}")
        except Exception as e:
            print(f"[خطا] نتونست {new_name} رو بسازه: {e}")

    print("-" * 55)
    print(f"تمام شد! {success} فایل با موفقیت ساخته شد داخل پوشه B")
    print("=" * 55)

if __name__ == "__main__":
    main()