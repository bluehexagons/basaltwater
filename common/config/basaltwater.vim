" Basaltwater terminal defaults. Personal overrides belong in ~/.vimrc.
filetype plugin indent on
syntax enable

" Show line numbers and keep the status line visible in a single window.
set number laststatus=2 ruler
set autoindent

" Keep edits when switching buffers and open new splits below or to the right.
set hidden splitbelow splitright

" Complete file and command names with Tab.
set wildmenu wildmode=longest:full,full

" Lowercase searches ignore case. An uppercase letter requests an exact case match.
set incsearch hlsearch ignorecase smartcase
nnoremap <silent> <C-L> :nohlsearch<CR><C-L>

" Send project matches, with line and column, to Vim's quickfix list.
if executable('rg')
  set grepprg=rg\ --vimgrep\ --smart-case\ --color=never
  set grepformat=%f:%l:%c:%m
endif
