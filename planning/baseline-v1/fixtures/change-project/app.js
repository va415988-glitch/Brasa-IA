const STORAGE_KEY = "reading-shelf-books";
const form = document.querySelector("#book-form");
const titleInput = document.querySelector("#book-title");
const bookList = document.querySelector("#book-list");
const emptyState = document.querySelector("#empty-state");
let books = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");

function save() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(books));
}

function render() {
  bookList.replaceChildren();
  emptyState.hidden = books.length > 0;
  for (const book of books) {
    const item = document.createElement("li");
    const title = document.createElement("span");
    title.textContent = book.title;
    const read = document.createElement("input");
    read.type = "checkbox";
    read.checked = book.read;
    read.setAttribute("aria-label", `Mark ${book.title} as read`);
    read.addEventListener("change", () => {
      book.read = read.checked;
      save();
    });
    item.append(title, read);
    bookList.append(item);
  }
}

form.addEventListener("submit", event => {
  event.preventDefault();
  const title = titleInput.value.trim();
  if (!title) return;
  books.push({ id: crypto.randomUUID(), title, read: false });
  save();
  render();
  form.reset();
  titleInput.focus();
});

render();
