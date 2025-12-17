"""
Debug скрипт за верификация на позициите на Слънцето и Меркурий
Тест данни: 1974-04-13, 10:30, Plovdiv (42.1354, 24.7453)
"""

import engine
from pathlib import Path

def main():
    print("=" * 60)
    print("Debug: Верификация на позициите на планетите")
    print("=" * 60)
    print("\nТест данни:")
    print("  Дата: 1974-04-13")
    print("  Час: 10:30")
    print("  Локация: Пловдив (42.1354, 24.7453)")
    print("\n" + "=" * 60)
    
    try:
        # Изчисляване на картата
        result = engine.calculate_chart(
            date="1974-04-13",
            time="10:30",
            lat=42.1354,
            lon=24.7453
        )
        
        print("\nРезултати:")
        print("-" * 60)
        
        # Проверка на Слънцето
        sun = result["planets"].get("Sun", {})
        if sun:
            print(f"\nСЛЪНЦЕ:")
            print(f"  Raw longitude: {sun.get('longitude', 'N/A')}")
            print(f"  Zodiac sign: {sun.get('zodiac_sign', 'N/A')}")
            print(f"  Formatted position: {sun.get('formatted_pos', 'N/A')}")
            print(f"  Speed: {sun.get('speed', 'N/A')}")
        else:
            print("\nСЛЪНЦЕ: Няма данни")
        
        # Проверка на Меркурий
        mercury = result["planets"].get("Mercury", {})
        if mercury:
            print(f"\nМЕРКУРИЙ:")
            print(f"  Raw longitude: {mercury.get('longitude', 'N/A')}")
            print(f"  Zodiac sign: {mercury.get('zodiac_sign', 'N/A')}")
            print(f"  Formatted position: {mercury.get('formatted_pos', 'N/A')}")
            print(f"  Speed: {mercury.get('speed', 'N/A')}")
        else:
            print("\nМЕРКУРИЙ: Няма данни")
        
        # Проверка на ASC и MC
        angles = result.get("angles", {})
        if angles:
            print(f"\nЪГЛИ:")
            asc = angles.get("Ascendant")
            mc = angles.get("MC")
            if asc is not None:
                print(f"  Ascendant raw: {asc}")
                print(f"  Ascendant formatted: {angles.get('Ascendant_formatted', 'N/A')}")
                print(f"  Ascendant sign: {angles.get('Ascendant_sign', 'N/A')}")
            if mc is not None:
                print(f"  MC raw: {mc}")
                print(f"  MC formatted: {angles.get('MC_formatted', 'N/A')}")
                print(f"  MC sign: {angles.get('MC_sign', 'N/A')}")
        
        # Очаквани резултати
        print("\n" + "=" * 60)
        print("Очаквани резултати:")
        print("  Слънце: ~23° Aries (longitude ~23)")
        print("  Меркурий: ~3° Aries (longitude ~3)")
        print("  MC: ~352° Pisces (longitude ~352)")
        print("=" * 60)
        
        # Проверка за разлика
        if sun.get('longitude') and mercury.get('longitude'):
            sun_long = sun.get('longitude')
            mercury_long = mercury.get('longitude')
            diff = abs(sun_long - mercury_long)
            print(f"\nРазлика между Слънце и Меркурий: {diff:.2f}°")
            if diff < 5:
                print("⚠ ВНИМАНИЕ: Разликата е твърде малка - възможна грешка!")
            else:
                print("✓ Разликата е разумна")
        
    except FileNotFoundError as e:
        print(f"\n✗ Грешка: {e}")
        print("\nМоля изпълнете първо:")
        print("  python scripts/download_ephe.py")
    except Exception as e:
        print(f"\n✗ Грешка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

