"""Tests for telemetry decorators."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestTracedNodeDecorator(unittest.IsolatedAsyncioTestCase):
    """Test traced_node decorator."""

    def test_import(self):
        """Test that decorator can be imported."""
        from app.progenitor.nodes import traced_node
        assert traced_node is not None

    async def test_basic_decoration(self):
        """Test basic node decoration without telemetry."""
        from app.progenitor.nodes.telemetry_decorators import traced_node

        @traced_node("test_node")
        def create_test_node():
            async def test_node(state):
                return {"result": "success"}
            return test_node

        node = create_test_node()
        result = await node({})

        assert result == {"result": "success"}

    async def test_preserves_return_value(self):
        """Test that decorator preserves node return value."""
        from app.progenitor.nodes.telemetry_decorators import traced_node

        @traced_node("data_node")
        def create_node():
            async def node(state):
                return {
                    "backlog": [{"id": "1"}],
                    "current_decision": {"action": "answer"},
                }
            return node

        node = create_node()
        result = await node({"cycle_count": 1})

        assert "backlog" in result
        assert "current_decision" in result

    async def test_passes_state_to_node(self):
        """Test that state is passed through to node."""
        from app.progenitor.nodes.telemetry_decorators import traced_node

        received_state = None

        @traced_node("receiver")
        def create_node():
            async def node(state):
                nonlocal received_state
                received_state = state
                return {}
            return node

        node = create_node()
        input_state = {"current_input": "hello", "cycle_count": 5}
        await node(input_state)

        assert received_state == input_state

    async def test_propagates_exceptions(self):
        """Test that exceptions are properly propagated."""
        from app.progenitor.nodes.telemetry_decorators import traced_node

        @traced_node("error_node")
        def create_node():
            async def node(state):
                raise ValueError("Test error")
            return node

        node = create_node()

        with self.assertRaises(ValueError) as ctx:
            await node({})

        assert "Test error" in str(ctx.exception)

    async def test_with_factory_args(self):
        """Test decoration with factory arguments."""
        from app.progenitor.nodes.telemetry_decorators import traced_node

        @traced_node("configurable")
        def create_node(multiplier: int):
            async def node(state):
                return {"value": state.get("input", 0) * multiplier}
            return node

        node = create_node(multiplier=3)
        result = await node({"input": 5})

        assert result["value"] == 15


class TestTracedMethodDecorator(unittest.IsolatedAsyncioTestCase):
    """Test traced_method decorator."""

    async def test_basic_decoration(self):
        """Test basic method decoration."""
        from app.progenitor.nodes.telemetry_decorators import traced_method

        class MyService:
            @traced_method("process")
            async def process(self, data: str) -> str:
                return f"processed: {data}"

        service = MyService()
        result = await service.process("test")

        assert result == "processed: test"

    async def test_custom_span_name(self):
        """Test with custom span name."""
        from app.progenitor.nodes.telemetry_decorators import traced_method

        class MyService:
            @traced_method("process", span_name="custom.process.span")
            async def process(self, data: str) -> str:
                return data

        service = MyService()
        result = await service.process("hello")

        assert result == "hello"


class TestTimedDecorator(unittest.IsolatedAsyncioTestCase):
    """Test timed decorator."""

    async def test_basic_timing(self):
        """Test basic timing decoration."""
        from app.progenitor.nodes.telemetry_decorators import timed

        @timed("test_operation")
        async def slow_operation():
            return "done"

        result = await slow_operation()
        assert result == "done"

    async def test_with_labels(self):
        """Test timing with custom labels."""
        from app.progenitor.nodes.telemetry_decorators import timed

        @timed("llm_call", {"model": "gpt-4", "endpoint": "chat"})
        async def call_model(prompt: str) -> str:
            return f"Response to: {prompt}"

        result = await call_model("Hello")
        assert result == "Response to: Hello"


class TestNodeInstrumentationMixin(unittest.TestCase):
    """Test NodeInstrumentationMixin."""

    def test_mixin_can_be_used(self):
        """Test that mixin can be inherited."""
        from app.progenitor.nodes.telemetry_decorators import NodeInstrumentationMixin

        class MyFactory(NodeInstrumentationMixin):
            def create_node(self):
                pass

        factory = MyFactory()
        assert hasattr(factory, "trace_operation")
        assert hasattr(factory, "record_metric")

    def test_trace_operation_context_manager(self):
        """Test trace_operation as context manager."""
        from app.progenitor.nodes.telemetry_decorators import NodeInstrumentationMixin

        class MyFactory(NodeInstrumentationMixin):
            def do_work(self):
                with self.trace_operation("test_op"):
                    return "work done"

        factory = MyFactory()
        result = factory.do_work()

        assert result == "work done"

    def test_record_metric(self):
        """Test record_metric method."""
        from app.progenitor.nodes.telemetry_decorators import NodeInstrumentationMixin

        class MyFactory(NodeInstrumentationMixin):
            pass

        factory = MyFactory()

        # Should not raise even without telemetry
        factory.record_metric("counter", "test_count")
        factory.record_metric("histogram", "test_duration", 1.5, {"label": "value"})


class TestTelemetryIntegration(unittest.IsolatedAsyncioTestCase):
    """Test telemetry decorator integration with mock telemetry."""

    async def test_metrics_recorded_with_mock(self):
        """Test that metrics are recorded when telemetry is available."""
        from app.progenitor.nodes.telemetry_decorators import traced_node

        mock_tracer = MagicMock()
        mock_tracer.start_span = MagicMock(return_value=MagicMock())
        mock_tracer.end_span = MagicMock()

        mock_metrics = MagicMock()
        mock_metrics.record_histogram = MagicMock()
        mock_metrics.increment = MagicMock()

        with patch(
            'app.progenitor.nodes.telemetry_decorators._get_telemetry',
            return_value=(mock_tracer, mock_metrics)
        ):
            @traced_node("instrumented_node")
            def create_node():
                async def node(state):
                    return {"done": True}
                return node

            node = create_node()
            await node({"cycle_count": 1})

            # Verify metrics were recorded
            mock_metrics.record_histogram.assert_called()
            mock_metrics.increment.assert_called()

    async def test_error_recorded_on_exception(self):
        """Test that errors are recorded when node raises."""
        from app.progenitor.nodes.telemetry_decorators import traced_node

        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_span.record_exception = MagicMock()
        mock_tracer.start_span = MagicMock(return_value=mock_span)

        mock_metrics = MagicMock()
        mock_metrics.increment = MagicMock()

        with patch(
            'app.progenitor.nodes.telemetry_decorators._get_telemetry',
            return_value=(mock_tracer, mock_metrics)
        ):
            @traced_node("failing_node")
            def create_node():
                async def node(state):
                    raise RuntimeError("Test failure")
                return node

            node = create_node()

            with self.assertRaises(RuntimeError):
                await node({})

            # Verify error metric was recorded
            calls = mock_metrics.increment.call_args_list
            error_calls = [c for c in calls if "error" in str(c)]
            assert len(error_calls) > 0


class TestDecoratorPreservation(unittest.IsolatedAsyncioTestCase):
    """Test that decorators preserve function metadata."""

    async def test_preserves_docstring(self):
        """Test that decorator preserves docstring."""
        from app.progenitor.nodes.telemetry_decorators import traced_node

        @traced_node("documented")
        def create_node():
            """Create a documented node."""
            async def node(state):
                """Process state and return updates."""
                return {}
            return node

        assert "Create a documented node" in create_node.__doc__

    async def test_preserves_function_name(self):
        """Test that decorator preserves function name."""
        from app.progenitor.nodes.telemetry_decorators import traced_node

        @traced_node("named")
        def create_special_node():
            async def special_node(state):
                return {}
            return special_node

        assert create_special_node.__name__ == "create_special_node"


if __name__ == "__main__":
    unittest.main()
