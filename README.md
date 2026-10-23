# blog.atomai.click

AWS와 Kubernetes 기술 글을 모은 정적 블로그. GitHub Pages(main 브랜치 루트)로 서비스합니다.

## 글 쓰기

1. `posts/<slug>.md`를 만듭니다. URL은 `https://blog.atomai.click/<slug>/`가 됩니다.

   ```
   ---
   title: "제목"
   date: 2026-10-20
   description: "목록과 검색 결과에 보일 한 문장"
   tags: eks, istio
   ---

   본문 (Markdown, 인라인 HTML 허용)
   ```

2. `python3 build.py`로 HTML을 다시 만듭니다(`pip install markdown` 필요).
3. 생성된 파일(`<slug>/index.html`, `index.html`, `tags/`, `feed.xml`, `sitemap.xml`)을 함께 커밋합니다.

생성된 HTML은 직접 고치지 말고 `posts/`나 `build.py`, `assets/style.css`를 고친 뒤 다시 빌드합니다.

## 본문에서 쓸 수 있는 스타일

- `<p class="lead">`: 도입 문단
- `<div class="callout">`, `callout warn`, `callout bad`: 첫 `<strong>`이 제목
- `<nav class="toc" markdown="1">`: 목차 (안에 Markdown 사용 가능)
- `<figure class="video"><video ...></video><figcaption>`: 영상

## 미리 보기

```
python3 build.py && python3 -m http.server 8080
```

생성된 HTML의 영상·이미지·스타일과 내부 링크는 페이지 위치를 기준으로 한 상대 경로를 사용합니다. HTML 파일을 직접 열어도 영상 경로가 유지됩니다. 브라우저의 로컬 파일 보안 정책 때문에 자막까지 확인하려면 위 HTTP 미리보기 서버를 사용하세요.
