import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Header from '../components/layout/Header';
import { useApi } from '../hooks/useApi';

function formatTimestamp(value) {
  if (!value) {
    return 'Not set';
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function formatAgentName(value) {
  return String(value || 'unassigned')
    .replace(/[_:]/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function parseLabels(labelsText) {
  return labelsText
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean);
}

function buildDraft(card) {
  if (!card) {
    return null;
  }

  return {
    ...card,
    labelsText: Array.isArray(card.labels) ? card.labels.join(', ') : '',
  };
}

function getStatusTone(status) {
  return `kanban-tone--${status || 'idle'}`;
}

function buildCardBadges(card) {
  return [
    card.spec_path ? { key: 'spec', label: 'Spec' } : null,
    card.branch_name ? { key: 'branch', label: card.branch_name } : null,
    card.pr_url ? { key: 'pr', label: 'PR' } : null,
    card.last_error ? { key: 'error', label: 'Blocked' } : null,
  ].filter(Boolean);
}

function isArchivedCard(card) {
  return Boolean(card?.metadata?.archived_at);
}

function resequenceBoardCards(cards, columns) {
  const columnOrder = new Map(columns.map((column, index) => [column.id, index]));
  const groupedCards = new Map(columns.map((column) => [column.id, []]));

  const orderedCards = [...cards].sort((left, right) => {
    const leftColumn = columnOrder.get(left.column_id) ?? Number.MAX_SAFE_INTEGER;
    const rightColumn = columnOrder.get(right.column_id) ?? Number.MAX_SAFE_INTEGER;
    if (leftColumn !== rightColumn) {
      return leftColumn - rightColumn;
    }
    if ((left.position ?? 0) !== (right.position ?? 0)) {
      return (left.position ?? 0) - (right.position ?? 0);
    }
    return String(left.created_at || '').localeCompare(String(right.created_at || ''));
  });

  orderedCards.forEach((card) => {
    const nextCards = groupedCards.get(card.column_id) || [];
    nextCards.push({ ...card });
    groupedCards.set(card.column_id, nextCards);
  });

  const normalizedCards = [];
  columns.forEach((column) => {
    (groupedCards.get(column.id) || []).forEach((card, index) => {
      normalizedCards.push({ ...card, position: index });
    });
  });
  return normalizedCards;
}

function moveCardInBoard(board, cardId, targetColumnId, targetPosition) {
  if (!board) {
    return board;
  }

  const groupedCards = new Map(board.columns.map((column) => [column.id, []]));
  let movingCard = null;

  resequenceBoardCards(board.cards, board.columns).forEach((card) => {
    if (card.id === cardId) {
      movingCard = { ...card };
      return;
    }
    const nextCards = groupedCards.get(card.column_id) || [];
    nextCards.push({ ...card });
    groupedCards.set(card.column_id, nextCards);
  });

  if (!movingCard) {
    return board;
  }

  const targetCards = [...(groupedCards.get(targetColumnId) || [])];
  const insertAt = Math.max(0, Math.min(targetPosition, targetCards.length));
  targetCards.splice(insertAt, 0, { ...movingCard, column_id: targetColumnId, position: insertAt });
  groupedCards.set(targetColumnId, targetCards);

  const cards = [];
  board.columns.forEach((column) => {
    (groupedCards.get(column.id) || []).forEach((card, index) => {
      cards.push({ ...card, position: index });
    });
  });

  return { ...board, cards };
}

function getDropPosition(columnCards, draggedCardId, targetCardId, edge) {
  const visibleCards = columnCards.filter((card) => card.id !== draggedCardId);
  const targetIndex = visibleCards.findIndex((card) => card.id === targetCardId);
  if (targetIndex === -1) {
    return visibleCards.length;
  }
  return edge === 'before' ? targetIndex : targetIndex + 1;
}

function isFormField(element) {
  if (!(element instanceof HTMLElement)) {
    return false;
  }

  return Boolean(element.closest('input, textarea, select, button, a, [role="button"]'));
}

function KanbanPage() {
  const {
    connectionState,
    getKanbanBoard,
    createKanbanCard,
    updateKanbanCard,
    deleteKanbanCard,
  } = useApi();
  const [board, setBoard] = useState(null);
  const [selectedCardId, setSelectedCardId] = useState(null);
  const [draft, setDraft] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState(false);
  const [movingCardId, setMovingCardId] = useState(null);
  const [actionCardId, setActionCardId] = useState(null);
  const [openMenuCardId, setOpenMenuCardId] = useState(null);
  const [draggedCardId, setDraggedCardId] = useState(null);
  const [dropIndicator, setDropIndicator] = useState(null);
  const [error, setError] = useState('');
  const [newCardTitle, setNewCardTitle] = useState('');
  const [newCardSummary, setNewCardSummary] = useState('');
  const [newCardDescription, setNewCardDescription] = useState('');
  const [newCardOwner, setNewCardOwner] = useState('user');
  const [newCardLabelsText, setNewCardLabelsText] = useState('');
  const [isCreatePanelOpen, setIsCreatePanelOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [collapsedColumns, setCollapsedColumns] = useState({
    backlog: false,
    done: false,
  });
  const newCardInputRef = useRef(null);
  const boardWrapRef = useRef(null);

  const loadBoard = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const nextBoard = await getKanbanBoard();
      setBoard(nextBoard);
      setSelectedCardId((current) => {
        if (current && nextBoard.cards.some((card) => card.id === current)) {
          return current;
        }
        return current;
      });
    } catch (loadError) {
      setError(loadError.message || 'Failed to load kanban board');
    } finally {
      setLoading(false);
    }
  }, [getKanbanBoard]);

  useEffect(() => {
    loadBoard();
  }, [loadBoard]);

  const selectedCard = useMemo(
    () => board?.cards.find((card) => card.id === selectedCardId) || null,
    [board, selectedCardId],
  );

  useEffect(() => {
    setDraft(buildDraft(selectedCard));
  }, [selectedCard]);

  useEffect(() => {
    if (!openMenuCardId) {
      return undefined;
    }

    const handlePointerDown = (event) => {
      if (!(event.target instanceof HTMLElement)) {
        return;
      }
      if (!event.target.closest('.kanban-card__menu-shell')) {
        setOpenMenuCardId(null);
      }
    };

    window.addEventListener('mousedown', handlePointerDown);
    return () => window.removeEventListener('mousedown', handlePointerDown);
  }, [openMenuCardId]);

  useEffect(() => {
    if (!boardWrapRef.current) {
      return;
    }

    const animationFrame = window.requestAnimationFrame(() => {
      const maxScrollLeft = Math.max(0, boardWrapRef.current.scrollWidth - boardWrapRef.current.clientWidth);
      if (boardWrapRef.current.scrollLeft > maxScrollLeft) {
        boardWrapRef.current.scrollLeft = maxScrollLeft;
      }
    });

    return () => window.cancelAnimationFrame(animationFrame);
  }, [board?.updated_at, board?.columns?.length, collapsedColumns]);

  const columns = useMemo(() => {
    if (!board) {
      return [];
    }

    const query = searchQuery.trim().toLowerCase();
    return board.columns.map((column) => ({
      ...column,
      cards: board.cards.filter((card) => {
        if (card.column_id !== column.id) {
          return false;
        }

        if (isArchivedCard(card)) {
          return false;
        }

        if (statusFilter !== 'all' && card.status !== statusFilter) {
          return false;
        }

        if (!query) {
          return true;
        }

        const haystack = [
          card.title,
          card.summary,
          card.description,
          ...(card.labels || []),
          card.agent_name,
          card.owner,
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase();

        return haystack.includes(query);
      }),
      isCollapsed: Boolean(collapsedColumns[column.id]),
    }));
  }, [board, collapsedColumns, searchQuery, statusFilter]);

  const handleDraftChange = useCallback((key, value) => {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  }, []);

  const handleToggleColumn = useCallback((columnId) => {
    if (!['backlog', 'done'].includes(columnId)) {
      return;
    }

    setCollapsedColumns((current) => ({
      ...current,
      [columnId]: !current[columnId],
    }));
  }, []);

  const handleCreateCard = useCallback(async (event) => {
    event.preventDefault();
    const title = newCardTitle.trim();
    if (!title) {
      setError('Backlog cards need a title.');
      return;
    }

    setCreating(true);
    setError('');
    try {
      const created = await createKanbanCard({
        title,
        summary: newCardSummary.trim(),
        description: newCardDescription.trim(),
        owner: newCardOwner.trim() || 'user',
        labels: parseLabels(newCardLabelsText),
      });
      setBoard((current) => (
        current
          ? { ...current, cards: [...current.cards, created], updated_at: created.updated_at }
          : current
      ));
      setSelectedCardId(created.id);
      setNewCardTitle('');
      setNewCardSummary('');
      setNewCardDescription('');
      setNewCardOwner('user');
      setNewCardLabelsText('');
      setIsCreatePanelOpen(false);
    } catch (createError) {
      setError(createError.message || 'Failed to create card');
    } finally {
      setCreating(false);
    }
  }, [createKanbanCard, newCardDescription, newCardLabelsText, newCardOwner, newCardSummary, newCardTitle]);

  const handleSaveCard = useCallback(async () => {
    if (!draft) {
      return;
    }

    setSaving(true);
    setError('');
    try {
      const updated = await updateKanbanCard(draft.id, {
        title: draft.title,
        summary: draft.summary,
        description: draft.description,
        column_id: draft.column_id,
        status: draft.status,
        agent_name: draft.agent_name,
        owner: draft.owner,
        labels: parseLabels(draft.labelsText),
        spec_path: draft.spec_path,
        branch_name: draft.branch_name,
        pr_url: draft.pr_url,
        last_error: draft.last_error,
      });
      setBoard((current) => (
        current
          ? {
            ...current,
            cards: current.cards.map((card) => (card.id === updated.id ? updated : card)),
            updated_at: updated.updated_at,
          }
          : current
      ));
      setDraft(buildDraft(updated));
    } catch (saveError) {
      setError(saveError.message || 'Failed to update card');
    } finally {
      setSaving(false);
    }
  }, [draft, updateKanbanCard]);

  const handleMoveCard = useCallback(async (cardId, targetColumnId, targetPosition) => {
    if (!board) {
      return;
    }

    const orderedCards = resequenceBoardCards(board.cards, board.columns);
    const card = orderedCards.find((item) => item.id === cardId);
    const targetColumn = board.columns.find((column) => column.id === targetColumnId);
    if (!card || !targetColumn) {
      return;
    }

    if (card.column_id === targetColumnId && card.position === targetPosition) {
      return;
    }

    const previousBoard = board;
    const now = new Date().toISOString();
    const optimisticCard = {
      ...card,
      column_id: targetColumnId,
      agent_name: targetColumn.agent_name,
      updated_at: now,
      position: targetPosition,
    };
    const optimisticBoard = moveCardInBoard(
      {
        ...board,
        cards: board.cards.map((item) => (item.id === cardId ? optimisticCard : item)),
      },
      cardId,
      targetColumnId,
      targetPosition,
    );

    setMovingCardId(cardId);
    setError('');
    setBoard({ ...optimisticBoard, updated_at: now });

    if (selectedCardId === cardId) {
      setDraft(buildDraft(optimisticBoard.cards.find((item) => item.id === cardId) || optimisticCard));
    }

    try {
      await updateKanbanCard(cardId, {
        column_id: targetColumnId,
        agent_name: targetColumn.agent_name,
        position: targetPosition,
      });
      const refreshedBoard = await getKanbanBoard();
      setBoard(refreshedBoard);
      if (selectedCardId === cardId) {
        setDraft(buildDraft(refreshedBoard.cards.find((item) => item.id === cardId) || null));
      }
    } catch (moveError) {
      setBoard(previousBoard);
      setError(moveError.message || 'Failed to move card');
      if (selectedCardId === cardId) {
        setDraft(buildDraft(card));
      }
    } finally {
      setMovingCardId(null);
      setDraggedCardId(null);
      setDropIndicator(null);
    }
  }, [board, getKanbanBoard, selectedCardId, updateKanbanCard]);

  const handleDragStart = useCallback((event, cardId) => {
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', cardId);
    setDraggedCardId(cardId);
  }, []);

  const handleDragEnd = useCallback(() => {
    setDraggedCardId(null);
    setDropIndicator(null);
  }, []);

  const handleDragOverColumn = useCallback((event, columnId) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    setDropIndicator({ columnId, cardId: null, edge: 'after' });
  }, []);

  const handleDropOnColumn = useCallback((event, columnId) => {
    event.preventDefault();
    const cardId = event.dataTransfer.getData('text/plain') || draggedCardId;
    if (cardId) {
      const columnCards = columns.find((column) => column.id === columnId)?.cards || [];
      handleMoveCard(cardId, columnId, columnCards.filter((card) => card.id !== cardId).length);
    }
  }, [columns, draggedCardId, handleMoveCard]);

  const handleDragOverCard = useCallback((event, columnId, cardId) => {
    event.preventDefault();
    event.stopPropagation();
    const bounds = event.currentTarget.getBoundingClientRect();
    const edge = event.clientY - bounds.top < bounds.height / 2 ? 'before' : 'after';
    event.dataTransfer.dropEffect = 'move';
    setDropIndicator({ columnId, cardId, edge });
  }, []);

  const handleDropOnCard = useCallback((event, columnId, targetCardId) => {
    event.preventDefault();
    event.stopPropagation();
    const cardId = event.dataTransfer.getData('text/plain') || draggedCardId;
    if (!cardId || cardId === targetCardId) {
      return;
    }
    const edge = dropIndicator?.cardId === targetCardId ? dropIndicator.edge : 'after';
    const columnCards = columns.find((column) => column.id === columnId)?.cards || [];
    const targetPosition = getDropPosition(columnCards, cardId, targetCardId, edge);
    handleMoveCard(cardId, columnId, targetPosition);
  }, [columns, draggedCardId, dropIndicator, handleMoveCard]);

  const handleOpenCard = useCallback((cardId) => {
    setSelectedCardId(cardId);
  }, []);

  const handleArchiveCard = useCallback(async (card) => {
    setActionCardId(card.id);
    setError('');
    setOpenMenuCardId(null);
    try {
      await updateKanbanCard(card.id, {
        metadata: {
          ...(card.metadata || {}),
          archived_at: new Date().toISOString(),
        },
      });
      if (selectedCardId === card.id) {
        setSelectedCardId(null);
      }
      const refreshedBoard = await getKanbanBoard();
      setBoard(refreshedBoard);
    } catch (archiveError) {
      setError(archiveError.message || 'Failed to archive card');
    } finally {
      setActionCardId(null);
    }
  }, [getKanbanBoard, selectedCardId, updateKanbanCard]);

  const handleDeleteCard = useCallback(async (cardId) => {
    setActionCardId(cardId);
    setError('');
    setOpenMenuCardId(null);
    try {
      await deleteKanbanCard(cardId);
      if (selectedCardId === cardId) {
        setSelectedCardId(null);
      }
      const refreshedBoard = await getKanbanBoard();
      setBoard(refreshedBoard);
    } catch (deleteError) {
      setError(deleteError.message || 'Failed to delete card');
    } finally {
      setActionCardId(null);
    }
  }, [deleteKanbanCard, getKanbanBoard, selectedCardId]);

  const handleMoveCardByStep = useCallback((card, step) => {
    const orderedCards = resequenceBoardCards(board?.cards || [], board?.columns || []);
    const siblings = orderedCards.filter((item) => item.column_id === card.column_id && !isArchivedCard(item));
    const currentIndex = siblings.findIndex((item) => item.id === card.id);
    const targetIndex = currentIndex + step;
    if (currentIndex === -1 || targetIndex < 0 || targetIndex >= siblings.length) {
      return;
    }
    setOpenMenuCardId(null);
    handleMoveCard(card.id, card.column_id, targetIndex);
  }, [board, handleMoveCard]);

  const handleToolbarNewCard = useCallback(() => {
    setCollapsedColumns((current) => ({
      ...current,
      backlog: false,
    }));
    setSelectedCardId(null);
    setNewCardTitle('');
    setNewCardSummary('');
    setNewCardDescription('');
    setNewCardOwner('user');
    setNewCardLabelsText('');
    setIsCreatePanelOpen(true);
    setTimeout(() => {
      newCardInputRef.current?.focus();
    }, 0);
  }, []);

  const handleCloseCreateModal = useCallback(() => {
    setIsCreatePanelOpen(false);
  }, []);

  const handleClearFilters = useCallback(() => {
    setSearchQuery('');
    setStatusFilter('all');
  }, []);

  return (
    <div className="dashboard-container">
      <Header connectionState={connectionState} />
      <main className="kanban-shell">
        <section className="kanban-toolbar">
          <div className="kanban-toolbar__actions">
            <button
              type="button"
              className={`kanban-toolbar__create-button ${isCreatePanelOpen ? 'is-active' : ''}`}
              onClick={() => {
                if (isCreatePanelOpen) {
                  handleCloseCreateModal();
                  return;
                }
                handleToolbarNewCard();
              }}
            >
              + New card
            </button>
            <button type="button" className="btn-secondary" onClick={loadBoard}>
              Refresh
            </button>
          </div>
          <div className="kanban-toolbar__filters">
            <input
              type="search"
              className="kanban-toolbar__search"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Search cards"
            />
            <select
              className="kanban-toolbar__select"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
            >
              <option value="all">All statuses</option>
              {board?.card_schema?.statuses.map((status) => (
                <option key={status.id} value={status.id}>{status.label}</option>
              ))}
            </select>
            <button type="button" className="btn-secondary" onClick={handleClearFilters}>
              Clear
            </button>
          </div>
        </section>

        {error ? <div className="kanban-alert">{error}</div> : null}

        <section ref={boardWrapRef} className="kanban-board-wrap">
          <div className="kanban-board">
            {loading ? <div className="kanban-board__empty">Loading board…</div> : null}

            {!loading && columns.map((column) => (
              <article
                key={column.id}
                className={[
                  'kanban-column',
                  column.isCollapsed ? 'is-collapsed' : '',
                  dropIndicator?.columnId === column.id && !dropIndicator?.cardId ? 'is-drop-target' : '',
                ].join(' ')}
                onDragOver={(event) => handleDragOverColumn(event, column.id)}
                onDrop={(event) => handleDropOnColumn(event, column.id)}
              >
                <header className="kanban-column__header">
                  <div className="kanban-column__title-row">
                    <div className="kanban-column__title-group">
                      <h3>{column.label}</h3>
                      <span className="kanban-column__count">{column.cards.length}</span>
                      {!column.isCollapsed ? (
                        <div className="kanban-column__info">
                          <button
                            type="button"
                            className="kanban-column__info-button"
                            aria-label={`${column.label} info`}
                          >
                            i
                          </button>
                          <div className="kanban-column__tooltip" role="tooltip">
                            {column.description}
                          </div>
                        </div>
                      ) : null}
                    </div>
                    {['backlog', 'done'].includes(column.id) ? (
                      <button
                        type="button"
                        className="kanban-column__toggle"
                        onClick={() => handleToggleColumn(column.id)}
                        aria-label={column.isCollapsed ? `Expand ${column.label}` : `Collapse ${column.label}`}
                      >
                        {column.isCollapsed ? '›' : '‹'}
                      </button>
                    ) : null}
                  </div>
                </header>

                {column.isCollapsed ? (
                  <div className="kanban-column__collapsed-body">
                    <span className="kanban-column__count kanban-column__count--collapsed">
                      {column.cards.length}
                    </span>
                    <div className="kanban-column__info">
                      <button
                        type="button"
                        className="kanban-column__info-button"
                        aria-label={`${column.label} info`}
                      >
                        i
                      </button>
                      <div className="kanban-column__tooltip" role="tooltip">
                        {column.description}
                      </div>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="kanban-column__cards">
                      {column.cards.map((card) => {
                        const badges = buildCardBadges(card);
                        return (
                          <article
                            key={card.id}
                            className={[
                              'kanban-card',
                              selectedCardId === card.id ? 'is-selected' : '',
                              draggedCardId === card.id ? 'is-dragging' : '',
                              movingCardId === card.id ? 'is-moving' : '',
                              dropIndicator?.cardId === card.id && dropIndicator?.edge === 'before' ? 'is-drop-before' : '',
                              dropIndicator?.cardId === card.id && dropIndicator?.edge === 'after' ? 'is-drop-after' : '',
                            ].join(' ')}
                            onDragOver={(event) => handleDragOverCard(event, column.id, card.id)}
                            onDrop={(event) => handleDropOnCard(event, column.id, card.id)}
                          >
                              <div className="kanban-card__topline">
                                <div
                                  className="kanban-card__topline-main"
                                  draggable
                                  role="presentation"
                                  onDragStart={(event) => handleDragStart(event, card.id)}
                                  onDragEnd={handleDragEnd}
                                >
                                  <div className="kanban-card__status-row">
                                    <span className={`kanban-chip ${getStatusTone(card.status)}`}>
                                      {card.status}
                                    </span>
                                  </div>
                                </div>
                                <div className="kanban-card__menu-shell">
                                  <button
                                    type="button"
                                    className="kanban-card__menu-trigger"
                                    aria-label={`Card actions for ${card.title}`}
                                    aria-expanded={openMenuCardId === card.id}
                                    onMouseDown={(event) => event.stopPropagation()}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      setOpenMenuCardId((current) => (current === card.id ? null : card.id));
                                    }}
                                  >
                                    ⋯
                                  </button>
                                  {openMenuCardId === card.id ? (
                                    <div className="kanban-card__menu" role="menu">
                                      <button
                                        type="button"
                                        className="kanban-card__menu-item kanban-card__menu-item--archive"
                                        role="menuitem"
                                        disabled={actionCardId === card.id}
                                        onClick={() => handleArchiveCard(card)}
                                      >
                                        Archive
                                      </button>
                                      <button
                                        type="button"
                                        className="kanban-card__menu-item"
                                        role="menuitem"
                                        disabled={actionCardId === card.id || card.position <= 0}
                                        onClick={() => handleMoveCardByStep(card, -1)}
                                      >
                                        Move up
                                      </button>
                                      <button
                                        type="button"
                                        className="kanban-card__menu-item"
                                        role="menuitem"
                                        disabled={
                                          actionCardId === card.id
                                          || card.position >= column.cards.length - 1
                                        }
                                        onClick={() => handleMoveCardByStep(card, 1)}
                                      >
                                        Move down
                                      </button>
                                      <button
                                        type="button"
                                        className="kanban-card__menu-item kanban-card__menu-item--danger"
                                        role="menuitem"
                                        disabled={actionCardId === card.id}
                                        onClick={() => handleDeleteCard(card.id)}
                                      >
                                        Delete
                                      </button>
                                    </div>
                                  ) : null}
                                </div>
                              </div>

                              <button
                                type="button"
                              className="kanban-card__content"
                              onClick={() => handleOpenCard(card.id)}
                              onKeyDown={(event) => {
                                if (event.key === 'Enter' || event.key === ' ') {
                                  event.preventDefault();
                                  handleOpenCard(card.id);
                                }
                              }}
                            >
                              <strong className="kanban-card__title">{card.title}</strong>
                              {card.summary ? <p className="kanban-card__summary">{card.summary}</p> : null}

                              {card.labels.length ? (
                                <div className="kanban-card__labels">
                                  {card.labels.map((label) => (
                                    <span key={label} className="kanban-label">{label}</span>
                                  ))}
                                </div>
                              ) : null}

                              {badges.length ? (
                                <div className="kanban-card__badges">
                                  {badges.map((badge) => (
                                    <span key={badge.key} className="kanban-badge">{badge.label}</span>
                                  ))}
                                </div>
                              ) : null}

                              <div className="kanban-card__footer">
                                <span>{formatAgentName(card.agent_name)}</span>
                                <span>{formatTimestamp(card.updated_at)}</span>
                              </div>
                            </button>
                          </article>
                        );
                      })}

                      {!column.cards.length ? (
                        <div className="kanban-column__empty">Drop cards here</div>
                      ) : null}
                    </div>
                  </>
                )}
              </article>
            ))}
          </div>
        </section>

        {draft ? (
          <div
            className="kanban-modal-backdrop"
            onClick={(event) => {
              if (!isFormField(event.target)) {
                setSelectedCardId(null);
              }
            }}
          >
            <section className="kanban-modal" onClick={(event) => event.stopPropagation()}>
              <header className="kanban-modal__header">
                <div>
                  <div className="kanban-modal__chips">
                    <span className={`kanban-chip ${getStatusTone(draft.status)}`}>{draft.status}</span>
                    <span className="kanban-badge">{draft.column_id}</span>
                  </div>
                  <h3>{draft.title}</h3>
                </div>
                <button type="button" className="kanban-modal__close" onClick={() => setSelectedCardId(null)}>
                  ×
                </button>
              </header>

              <div className="kanban-modal__body">
                <label>
                  <span>Title</span>
                  <input value={draft.title} onChange={(event) => handleDraftChange('title', event.target.value)} />
                </label>
                <label>
                  <span>Summary</span>
                  <textarea
                    rows="3"
                    value={draft.summary}
                    onChange={(event) => handleDraftChange('summary', event.target.value)}
                  />
                </label>
                <label>
                  <span>Description</span>
                  <textarea
                    rows="6"
                    value={draft.description}
                    onChange={(event) => handleDraftChange('description', event.target.value)}
                  />
                </label>

                <div className="kanban-modal__grid">
                  <label>
                    <span>Column</span>
                    <select value={draft.column_id} onChange={(event) => handleDraftChange('column_id', event.target.value)}>
                      {board?.columns.map((column) => (
                        <option key={column.id} value={column.id}>{column.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Status</span>
                    <select value={draft.status} onChange={(event) => handleDraftChange('status', event.target.value)}>
                      {board?.card_schema?.statuses.map((status) => (
                        <option key={status.id} value={status.id}>{status.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Assigned agent</span>
                    <input value={draft.agent_name} onChange={(event) => handleDraftChange('agent_name', event.target.value)} />
                  </label>
                  <label>
                    <span>Owner</span>
                    <input value={draft.owner} onChange={(event) => handleDraftChange('owner', event.target.value)} />
                  </label>
                </div>

                <div className="kanban-modal__grid">
                  <label>
                    <span>Spec file</span>
                    <input value={draft.spec_path || ''} onChange={(event) => handleDraftChange('spec_path', event.target.value)} />
                  </label>
                  <label>
                    <span>Branch</span>
                    <input value={draft.branch_name || ''} onChange={(event) => handleDraftChange('branch_name', event.target.value)} />
                  </label>
                  <label>
                    <span>Pull request</span>
                    <input value={draft.pr_url || ''} onChange={(event) => handleDraftChange('pr_url', event.target.value)} />
                  </label>
                  <label>
                    <span>Labels</span>
                    <input
                      value={draft.labelsText}
                      onChange={(event) => handleDraftChange('labelsText', event.target.value)}
                      placeholder="frontend, polish"
                    />
                  </label>
                </div>

                <label>
                  <span>Last error</span>
                  <textarea
                    rows="4"
                    value={draft.last_error || ''}
                    onChange={(event) => handleDraftChange('last_error', event.target.value)}
                  />
                </label>

                <div className="kanban-modal__timestamps">
                  <span>Created {formatTimestamp(draft.created_at)}</span>
                  <span>Updated {formatTimestamp(draft.updated_at)}</span>
                </div>
              </div>

              <footer className="kanban-modal__footer">
                <button type="button" className="btn-secondary" onClick={() => setSelectedCardId(null)}>
                  Close
                </button>
                <button type="button" className="btn-primary" onClick={handleSaveCard} disabled={saving}>
                  {saving ? 'Saving…' : 'Save card'}
                </button>
              </footer>
            </section>
          </div>
        ) : null}

        {isCreatePanelOpen ? (
          <div
            className="kanban-modal-backdrop"
            onClick={(event) => {
              if (!isFormField(event.target)) {
                handleCloseCreateModal();
              }
            }}
          >
            <form className="kanban-modal kanban-modal--create" onClick={(event) => event.stopPropagation()} onSubmit={handleCreateCard}>
              <header className="kanban-modal__header">
                <div>
                  <div className="kanban-modal__chips">
                    <span className="kanban-chip kanban-tone--idle">idle</span>
                    <span className="kanban-badge">backlog</span>
                  </div>
                  <h3>Add card</h3>
                </div>
                <button type="button" className="kanban-modal__close" onClick={handleCloseCreateModal}>
                  ×
                </button>
              </header>

              <div className="kanban-modal__body">
                <label>
                  <span>Title</span>
                  <input
                    ref={newCardInputRef}
                    value={newCardTitle}
                    onChange={(event) => setNewCardTitle(event.target.value)}
                    placeholder="Card title"
                  />
                </label>
                <label>
                  <span>Summary</span>
                  <textarea
                    rows="3"
                    value={newCardSummary}
                    onChange={(event) => setNewCardSummary(event.target.value)}
                    placeholder="Optional summary"
                  />
                </label>
                <label>
                  <span>Description</span>
                  <textarea
                    rows="6"
                    value={newCardDescription}
                    onChange={(event) => setNewCardDescription(event.target.value)}
                    placeholder="Details for this card"
                  />
                </label>

                <div className="kanban-modal__grid">
                  <label>
                    <span>Owner</span>
                    <input value={newCardOwner} onChange={(event) => setNewCardOwner(event.target.value)} />
                  </label>
                  <label>
                    <span>Labels</span>
                    <input
                      value={newCardLabelsText}
                      onChange={(event) => setNewCardLabelsText(event.target.value)}
                      placeholder="frontend, polish"
                    />
                  </label>
                </div>
              </div>

              <footer className="kanban-modal__footer">
                <button type="button" className="btn-secondary" onClick={handleCloseCreateModal}>
                  Close
                </button>
                <button type="submit" className="btn-primary" disabled={creating}>
                  {creating ? 'Adding…' : 'Add'}
                </button>
              </footer>
            </form>
          </div>
        ) : null}
      </main>
    </div>
  );
}

export default KanbanPage;
