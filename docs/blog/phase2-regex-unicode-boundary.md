# Python re 모듈의 \b 경계가 한국어에서 동작하지 않는 이유

## 문제 상황

LLM-OPT 프로젝트의 Validation Layer를 구현하면서, 쿼리에 연도나 버전 번호가 포함되어 있는지 감지하는 정규식을 작성했다.

연도는 `1900~2099`, 버전은 `1.2.3` 또는 `v1.2.3` 형태다. 처음에 이렇게 짰다.

```python
import re

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b")
```

그리고 테스트를 돌렸다.

```python
>>> _YEAR_RE.findall("2024년 한국 GDP 성장률은?")
['20']  # 기대: ['2024']

>>> _YEAR_RE.findall("Python 3.11 릴리즈 날짜는?")
[]      # 기대: ['2024'] (연도가 없으니 맞긴 한데...)
```

두 가지 문제가 터졌다.

1. `findall`이 `'2024'`가 아니라 `'20'`을 반환했다.
2. `"2024년"` 같은 한국어 텍스트에서는 `\b`가 의도대로 동작하지 않을 수 있다는 의심이 생겼다.

환경: Python 3.11, 한국어/영어 혼용 텍스트

---

## 원인 분석

### 문제 1: findall과 캡처 그룹

`(19|20)\d{2}` 에서 `(19|20)`이 **캡처 그룹**이다.

`re.findall()`은 패턴에 캡처 그룹이 있으면 **전체 매치가 아니라 캡처 그룹의 값만 반환**한다.

```python
re.findall(r"(19|20)\d{2}", "2024")
# → ['20']  ← 캡처 그룹 "(19|20)"의 매치값

re.findall(r"(?:19|20)\d{2}", "2024")
# → ['2024']  ← 비캡처 그룹, 전체 매치 반환
```

공식 문서에 명시된 동작이지만, 실수하기 쉬운 함정이다.

### 문제 2: \b 경계와 유니코드 모드

Python 3의 `re` 모듈은 기본적으로 **유니코드 모드**다.

유니코드 모드에서 `\w`는 유니코드 문자 전체를 포함한다. 한국어 `년`, `버전` 같은 문자도 `\w`로 취급된다.

`\b`(단어 경계)는 `\w`와 `\W`의 경계에서 발생한다. 따라서:

```
"2024년"
   ^  ^
   |  | → '년'은 \w이므로 '4'와 '년' 사이에 \b가 없음
   | → '2024' 앞(공백 또는 문장 시작)에 \b 발생
```

즉, `\b(?:19|20)\d{2}\b`를 `"2024년 GDP"`에 적용하면:

- `2024` 앞: `\b` 성공 (공백→숫자 경계)
- `2024` 뒤: `\b` **실패** (`4` 다음이 `년`, 둘 다 `\w`)

```python
>>> re.findall(r"\b(?:19|20)\d{2}\b", "2024년 GDP")
[]  # 매치 없음

>>> re.findall(r"\b(?:19|20)\d{2}\b", "GDP 2024")
['2024']  # 뒤에 공백이면 성공
```

한국어 텍스트에서는 `"2024년"` 형태가 자연스러운데, 이 경우 연도를 아예 검출하지 못한다.

### 해결책 탐색

`re.ASCII` 플래그를 사용하면 `\w`, `\b`가 ASCII 문자(`[a-zA-Z0-9_]`)만을 기준으로 동작한다.

ASCII 모드에서는 한국어 `년`이 `\W`로 취급되므로, `4`(ASCII `\w`)와 `년`(ASCII `\W`) 사이에 `\b`가 생긴다.

```python
>>> re.findall(r"\b(?:19|20)\d{2}\b", "2024년 GDP", re.ASCII)
['2024']  # 성공
```

---

## 시도한 해결책들

### 시도 1: 캡처 그룹 → 비캡처 그룹

```python
# 변경 전
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

# 변경 후
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
```

**결과:** `findall`이 전체 매치값을 반환하도록 수정. 부분적으로 해결됐지만 한국어 텍스트에서 `\b` 문제가 남아 있었다.

### 시도 2: re.ASCII 플래그 추가

```python
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b", re.ASCII)
_VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b", re.ASCII)
```

**결과:** 두 문제 모두 해결.

---

## 최종 해결

```python
# validator.py

# 연도: 1900~2099
# re.ASCII: 한국어 "년" 등이 \w로 인식되는 유니코드 모드 방지
# (?:19|20): 비캡처 그룹 → findall이 전체 매치 "2024" 반환 (캡처 그룹이면 "20"만 반환됨)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b", re.ASCII)
# 버전: v1.2.3 또는 1.2.3 형태
_VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b", re.ASCII)
```

검증:

```python
>>> _YEAR_RE.findall("2024년 GDP 성장률은?")
['2024']  ✅

>>> _YEAR_RE.findall("Python 3.11 릴리즈")
[]  ✅ (3.11은 버전이므로 연도 미감지, 의도대로)

>>> _VERSION_RE.findall("Python 3.11.2 설치 방법")
['3.11.2']  ✅

>>> _YEAR_RE.findall("2024년 vs 2023년 비교")
['2024', '2023']  ✅
```

이 패턴이 실제로 쓰이는 맥락:

```python
# Validation Layer: "2024년 GDP"와 "2023년 GDP"는 연도가 달라 캐시 미스로 처리
query_numbers = set(_YEAR_RE.findall(query_text)) | set(_VERSION_RE.findall(query_text))
cached_numbers = set(_YEAR_RE.findall(cached_text)) | set(_VERSION_RE.findall(cached_text))

if query_numbers != cached_numbers:
    return ValidationResult(passed=False, reason=f"numeric_mismatch: ...")
```

---

## 배운 점

**1. `re.findall`과 캡처 그룹은 조합에 주의해야 한다**

캡처 그룹이 있으면 `findall`은 그룹 값만 반환한다. 교대(alternation)에는 항상 비캡처 그룹 `(?:...)` 을 쓰는 습관을 들이자.

**2. Python 3 re 모듈의 기본은 유니코드 모드다**

영어 전용 정규식을 작성할 때와 달리, 한국어/일본어/중국어 등 다국어 텍스트를 다룰 때는 `\b`가 직관과 다르게 동작할 수 있다.

연도·버전처럼 **ASCII 숫자**를 감지하는 패턴은 `re.ASCII` 플래그를 명시하면 의도대로 동작한다.

**3. 주석으로 이유를 명시하자**

`re.ASCII`를 왜 쓰는지, `(?:...)` 를 왜 쓰는지 주석 없이 코드만 남기면 나중에 헷갈린다. 실제로 코드에 이유를 달아 두었더니 리뷰할 때 바로 이해할 수 있었다.

---

**참고**

- [Python docs — re — `re.ASCII`](https://docs.python.org/3/library/re.html#re.ASCII)
- [Python docs — re — `re.findall`](https://docs.python.org/3/library/re.html#re.findall)
